# -*- coding: utf-8 -*-
"""
每交易日韩股收盘后自动跑：抓数据 -> 算分 -> 追加历史 -> 生成 index.html + status.json
本地: python refresh.py    云端: GitHub Actions cron
"""
import os, json, csv, sys, html
from datetime import datetime, timezone, timedelta
import config as C

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
def P(*a): return os.path.join(HERE, *a)

def safe(fn, what, default=None):
    try:
        return fn()
    except Exception as e:
        print(f"[warn] {what}: {type(e).__name__}: {str(e)[:100]}", file=sys.stderr)
        return default

# ---------------- 数据抓取 ----------------
def fetch_market():
    import FinanceDataReader as fdr
    start = (datetime.now(timezone.utc) - timedelta(days=70)).strftime("%Y-%m-%d")
    m = {}
    def last_prev(sym):
        df = fdr.DataReader(sym, start)
        df = df.dropna(subset=["Close"])
        return float(df["Close"].iloc[-1]), (float(df["Close"].iloc[-2]) if len(df) > 1 else None), df.index[-1].date().isoformat()
    kp = safe(lambda: last_prev("KS11"), "KOSPI")
    if kp: m["kospi"], m["kospi_prev"], m["date"] = kp
    k2 = safe(lambda: last_prev("KS200"), "KOSPI200")
    if k2: m["k200"] = k2[0]
    ss = safe(lambda: last_prev("005930"), "Samsung")
    if ss: m["samsung"] = ss[0]
    hx = safe(lambda: last_prev("000660"), "Hynix")
    if hx: m["hynix"] = hx[0]
    fx = safe(lambda: fdr.DataReader("USD/KRW", start).dropna(subset=["Close"]), "USDKRW")
    if fx is not None and len(fx):
        m["usdkrw"] = float(fx["Close"].iloc[-1])
        if len(fx) > 21:
            m["usdkrw_mom"] = (float(fx["Close"].iloc[-1]) / float(fx["Close"].iloc[-22]) - 1) * 100
    # 市值集中度 + 广度
    lst = safe(lambda: fdr.StockListing("KOSPI"), "Listing")
    if lst is not None and "Marcap" in lst.columns:
        try:
            total = float(lst["Marcap"].sum())
            ssm = float(lst.loc[lst["Code"] == "005930", "Marcap"].iloc[0])
            hxm = float(lst.loc[lst["Code"] == "000660", "Marcap"].iloc[0])
            m["concentration"] = (ssm + hxm) / total
            m["mcap_samsung_tn"] = ssm / 1e12
            m["mcap_hynix_tn"] = hxm / 1e12
        except Exception as e:
            print(f"[warn] concentration: {e}", file=sys.stderr)
        try:
            cr = lst["ChagesRatio"].dropna()
            adv = int((cr > 0).sum()); dec = int((cr < 0).sum())
            m["adv"], m["dec"] = adv, dec
            if adv + dec > 0: m["decl_frac"] = dec / (adv + dec)
        except Exception as e:
            print(f"[warn] breadth: {e}", file=sys.stderr)
    # 双雄区间收益(分化)
    def ret1m(sym):
        df = fdr.DataReader(sym, start).dropna(subset=["Close"])
        if len(df) > 21: return float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-22]) - 1
        return None
    m["samsung_ret"] = safe(lambda: ret1m("005930"), "Samsung ret")
    m["hynix_ret"] = safe(lambda: ret1m("000660"), "Hynix ret")
    if "kospi" in m and m.get("kospi_prev"):
        m["index_up"] = m["kospi"] > m["kospi_prev"]
        m["kospi_chg"] = (m["kospi"] / m["kospi_prev"] - 1) * 100
    return m

def fetch_vkospi():
    """抓 VKOSPI(investing.com KSVKOSPI) 当前值；失败返回 None（由 state.json 沿用值兜底）。
    有 SCRAPERAPI_KEY(环境变量/GitHub secret) 时经 ScraperAPI 住宅代理抓取(云端可绕过 Cloudflare 403)，
    否则直连(仅本地住宅 IP 可成)。"""
    import re, os, requests
    ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
          "Accept-Language": "en-US,en;q=0.9"}
    key = os.environ.get("SCRAPERAPI_KEY")
    def parse(txt):
        m = re.search(r'instrument-price-last">([0-9.,]+)<', txt) or re.search(r'"last"\s*:\s*"?([0-9.]+)', txt)
        if m:
            v = float(m.group(1).replace(",", ""))
            if 1 < v < 300:
                return round(v, 2)
        return None
    for url in ("https://www.investing.com/indices/kospi-volatility", "https://kr.investing.com/indices/kospi-volatility"):
        try:
            if key:
                r = requests.get("https://api.scraperapi.com/",
                                 params={"api_key": key, "url": url, "country_code": "us"}, timeout=70)
            else:
                r = requests.get(url, headers=ua, timeout=20)
            if r.status_code == 200:
                v = parse(r.text)
                if v:
                    return v
                print(f"[warn] vkospi {url}: 200 但未解析到价格", file=sys.stderr)
            else:
                print(f"[warn] vkospi {'(scraperapi) ' if key else ''}{url} HTTP {r.status_code}", file=sys.stderr)
        except Exception as e:
            print(f"[warn] vkospi {url}: {type(e).__name__}: {str(e)[:60]}", file=sys.stderr)
    return None

# ---------------- 算分 ----------------
def auto_score(ind, m, state):
    a = ind["auto"]
    if a == "usdkrw":        return C.score_usdkrw(m.get("usdkrw"), m.get("usdkrw_mom"))
    if a == "concentration": return C.score_concentration(m.get("concentration"))
    if a == "breadth":       return C.score_breadth(m.get("decl_frac"), m.get("index_up"))
    if a == "hynix_zone":    return C.score_hynix_zone(m.get("hynix"), state.get("hynix_lower",2450000), state.get("hynix_upper",2500000))
    if a == "divergence":    return C.score_divergence(m.get("samsung_ret"), m.get("hynix_ret"))
    if a == "vkospi":        return C.score_vkospi(state.get("vkospi"))
    return None

def compute(state, m):
    rows = []
    for ind in C.INDICATORS:
        if ind["kind"] == "auto":
            e = auto_score(ind, m, state)
            esrc = "auto"
        else:
            e = state.get("manual", {}).get(ind["key"])
            esrc = "manual"
        if e is None:
            h = 0.0; e_disp = None
        else:
            h = ind["w"] * (5 - e) / 5 if ind["dir"] == "逆向" else ind["w"] * e / 5
            e_disp = e
        rows.append({**ind, "e": e_disp, "h": round(h, 2), "esrc": esrc})
    total = round(sum(r["h"] for r in rows), 1)
    cat = {}
    for c in C.CATS:
        wsum = sum(r["w"] for r in rows if r["cat"] == c)
        hsum = sum(r["h"] for r in rows if r["cat"] == c)
        cat[c] = round(hsum / wsum * 100, 1) if wsum else 0
    # 触发器
    k200 = state.get("k200_status", "")
    t = {}
    t["T1"] = state.get("flow_status") == "连续净卖/创纪录" and k200 != "创新高/强势"
    t["T2"] = state.get("domestic_status") in ("边际减弱", "接不动/枯竭")
    t["T3"] = cat["杠杆/强平"] >= 80
    t["T4"] = k200 in ("跌破后反抽失败", "周线破位反抽失败")
    t["T5"] = (m.get("hynix") is not None) and (m["hynix"] >= state.get("hynix_lower", 2450000))
    trig = sum(1 for v in t.values() if v)
    # 置信度: auto 永远新鲜; manual 看 manual_update 距今
    mu = state.get("manual_update")
    days = None
    if mu:
        try: days = (datetime.now(KST).date() - datetime.fromisoformat(mu).date()).days
        except Exception: days = None
    manual_fresh = (days is not None and days <= C.FRESH_DAYS)
    n_auto = sum(1 for r in rows if r["esrc"] == "auto" and r["e"] is not None)
    n_manual = sum(1 for r in rows if r["esrc"] == "manual" and r["e"] is not None)
    n_total = sum(1 for r in rows if r["e"] is not None)
    conf = round((n_auto + (n_manual if manual_fresh else 0)) / n_total, 2) if n_total else 0
    # gating
    vk = state.get("vkospi")
    iv_label, iv_text, iv_factor, iv_color = C.iv_gate(vk)
    gate_first = (total >= C.GATE_TOTAL_MIN and cat["价格行为"] >= C.GATE_PRICE_CAT_MIN
                  and trig >= 3 and t["T4"] and conf >= 0.5)
    gate_add = (total >= C.GATE_ADD_TOTAL_MIN and trig >= 4 and t["T4"]
                and cat["杠杆/强平"] >= 70 and conf >= 0.8)
    short, full, bcolor = C.band(total)
    if 70 <= total < 82 and not gate_first:
        short = "第一笔·待右侧"
    # 今日动作
    if not gate_first:
        pre = f"VKOSPI {vk} 极高 → 只挂 put 价差报价+备清单；" if (vk and vk >= 60) else ""
        action = f"今日：不建仓。{pre}等『K200 破位反抽失败』且价格行为类≥60 再动手"
    elif gate_add:
        action = f"今日：去杠杆确认 → 按 Playbook 加仓/移仓（仓位×{iv_factor}）"
    else:
        tool = "put 价差" if (vk and vk >= 45) else "裸 put"
        action = f"今日：满足放行 → 下第一笔试探仓（{tool}，仓位×{iv_factor}）"
    rightside = ("✅ K200 已破位反抽失败，右侧确认 — 可执行" if t["T4"]
                 else "⚠️ K200 未破位：即使分数到顶，今日上限 = 准备/观察，不建仓（右侧总闸）")
    return dict(rows=rows, total=total, cat=cat, trig=t, trig_n=trig, conf=conf,
                manual_days=days, manual_fresh=manual_fresh, iv_label=iv_label, iv_text=iv_text,
                iv_factor=iv_factor, iv_color=iv_color, gate_first=gate_first, gate_add=gate_add,
                short=short, full=full, bcolor=bcolor, action=action, rightside=rightside)

# ---------------- 历史 ----------------
HIST = P("data_history.csv")
HCOLS = ["date","kospi","kospi_chg","k200","samsung","hynix","usdkrw","concentration","adv","dec","decl_frac","vkospi","total","status"]
def append_history(m, r, state):
    date = m.get("date")
    if not date: return
    existing = []
    if os.path.exists(HIST):
        with open(HIST, encoding="utf-8") as f:
            existing = list(csv.DictReader(f))
    row = {"date": date, "kospi": m.get("kospi"), "kospi_chg": round(m["kospi_chg"],2) if "kospi_chg" in m else "",
           "k200": m.get("k200"), "samsung": m.get("samsung"), "hynix": m.get("hynix"),
           "usdkrw": m.get("usdkrw"), "concentration": round(m["concentration"],4) if "concentration" in m else "",
           "adv": m.get("adv"), "dec": m.get("dec"),
           "decl_frac": round(m["decl_frac"],3) if "decl_frac" in m else "",
           "vkospi": state.get("vkospi"), "total": r["total"], "status": r["short"]}
    existing = [e for e in existing if e.get("date") != date]   # dedupe by date
    existing.append({k: ("" if row.get(k) is None else row.get(k)) for k in HCOLS})
    existing.sort(key=lambda e: e.get("date",""))
    with open(HIST, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HCOLS); w.writeheader(); w.writerows(existing[-400:])
    return existing[-30:]

# ---------------- 渲染 ----------------
def esc(x): return html.escape(str(x)) if x is not None else ""
def fmt(x, n=0):
    try: return f"{float(x):,.{n}f}"
    except Exception: return "—"

def render(m, r, state, hist_tail):
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    ddate = m.get("date", "—")
    vk_src = "自动·investing" if state.get("_vk_src") == "auto" else "沿用·手填"
    # 触发器行
    trig_labels = {
        "T1":"① 外资连续净卖且 K200 不再创新高",
        "T2":"② 国内接盘枯竭（散户/被动盘转弱）",
        "T3":"③ 杠杆反噬（杠杆/强平类≥80）",
        "T4":"④ K200 破位反抽失败【右侧总闸】",
        "T5":"⑤ Hynix 收盘≥观察下沿(清仓区)",
    }
    trig_html = "".join(
        f'<div class="trig {"on" if r["trig"][k] else "off"}"><span>{esc(v)}</span>'
        f'<b>{"✅" if r["trig"][k] else "⬜"}</b></div>' for k,v in trig_labels.items())
    # 类别条
    cat_html = ""
    for c in C.CATS:
        v = r["cat"][c]
        col = "#C00000" if v>=80 else "#ED7D31" if v>=60 else "#FFC000" if v>=40 else "#63BE7B"
        cat_html += (f'<div class="catrow"><span>{esc(c)}</span>'
                     f'<div class="bar"><i style="width:{min(100,v):.0f}%;background:{col}"></i></div>'
                     f'<b>{v:.0f}</b></div>')
    # 指标明细
    def srcbadge(s): return '<em class="auto">自动</em>' if s=="auto" else '<em class="man">手填</em>'
    ind_html = ""
    cur = None
    for row in r["rows"]:
        if row["cat"] != cur:
            cur = row["cat"]; ind_html += f'<tr class="cathead"><td colspan="4">{esc(cur)}</td></tr>'
        e = "—" if row["e"] is None else (f'{row["e"]:.1f}')
        ind_html += (f'<tr><td>{esc(row["name"])} {srcbadge(row["esrc"])}</td>'
                     f'<td class="c">{row["w"]}</td><td class="c">{e}</td><td class="c">{row["h"]:.1f}</td></tr>')
    # 自动数据卡
    auto_cards = [
        ("KOSPI", f'{fmt(m.get("kospi"),0)}' + (f' <small>({m["kospi_chg"]:+.2f}%)</small>' if "kospi_chg" in m else "")),
        ("KOSPI200", fmt(m.get("k200"),1)),
        ("SK Hynix", fmt(m.get("hynix"),0)),
        ("Samsung", fmt(m.get("samsung"),0)),
        ("USD/KRW", fmt(m.get("usdkrw"),1)),
        ("双雄市值占比", f'{m["concentration"]*100:.1f}%' if "concentration" in m else "—"),
        ("广度(涨/跌)", f'{m.get("adv","—")}/{m.get("dec","—")}'),
        (f"VKOSPI({vk_src})", state.get("vkospi","—")),
    ]
    cards_html = "".join(f'<div class="card"><span>{esc(k)}</span><b>{v}</b></div>' for k,v in auto_cards)
    # 手动新鲜度
    md = r["manual_days"]
    if md is None: man_warn = '⚠️ 手动输入从未标注更新日期（state.json 的 manual_update）'
    elif r["manual_fresh"]: man_warn = f'手动输入 {md} 天前更新（≤{C.FRESH_DAYS} 天，新鲜）'
    else: man_warn = f'⚠️ 手动输入已 {md} 天未更新（>{C.FRESH_DAYS} 天）— 置信度已下调，更新后再据此建仓'
    # 历史表
    hist_html = ""
    for h in reversed(hist_tail[-12:]):
        hist_html += (f'<tr><td>{esc(h.get("date"))}</td><td class="c">{esc(h.get("kospi"))}</td>'
                      f'<td class="c">{esc(h.get("kospi_chg"))}</td><td class="c">{esc(h.get("hynix"))}</td>'
                      f'<td class="c">{esc(h.get("decl_frac"))}</td><td class="c">{esc(h.get("total"))}</td>'
                      f'<td class="c">{esc(h.get("status"))}</td></tr>')

    return f"""<!doctype html><html lang="zh"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>韩国去杠杆做空择时评分卡</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#0f1115;color:#e6e6e6;
 max-width:520px;margin:0 auto;padding:12px 12px 40px;font-size:15px;line-height:1.5}}
h1{{font-size:18px;font-weight:700}} .sub{{color:#8b93a7;font-size:11px;margin-bottom:10px}}
.sec{{background:#1a1d27;border-radius:12px;padding:12px;margin:10px 0;box-shadow:0 1px 3px #0006}}
.sechd{{font-size:12px;color:#9aa3b8;font-weight:700;margin-bottom:8px;letter-spacing:.5px}}
.total{{display:flex;align-items:center;gap:14px}}
.bignum{{font-size:54px;font-weight:800;line-height:1}}
.statusbox{{padding:10px 14px;border-radius:10px;color:#fff;font-weight:700;font-size:16px;text-align:center;min-width:96px}}
.full{{margin-top:8px;font-size:14px;color:#cfd6e6}}
.right{{margin-top:8px;font-size:13px;font-weight:600}}
.action{{background:#2a2410;border:1px solid #5a4d1a;border-radius:10px;padding:11px;font-size:14px;font-weight:600;color:#ffe9a8}}
.trig{{display:flex;justify-content:space-between;align-items:center;padding:7px 4px;border-bottom:1px solid #262a36;font-size:13px}}
.trig:last-child{{border:0}} .trig b{{font-size:15px}} .trig.on{{color:#ff9a9a}} .trig.off{{color:#7f8aa3}}
.trigtot{{text-align:right;font-size:20px;font-weight:800;margin-top:4px}}
.ivbox{{padding:10px;border-radius:10px;color:#fff}}
.ivbox b{{font-size:16px}} .ivbox p{{font-size:12px;margin-top:4px;opacity:.95}}
.catrow{{display:flex;align-items:center;gap:8px;padding:5px 0;font-size:13px}}
.catrow span{{width:110px;flex:none}} .catrow b{{width:34px;text-align:right}}
.bar{{flex:1;background:#262a36;border-radius:6px;height:12px;overflow:hidden}} .bar i{{display:block;height:100%}}
.cards{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
.card{{background:#11141c;border-radius:9px;padding:8px 10px;display:flex;flex-direction:column}}
.card span{{font-size:11px;color:#8b93a7}} .card b{{font-size:17px;margin-top:2px}} .card small{{font-size:11px;color:#8b93a7}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{padding:5px 6px;border-bottom:1px solid #232734;text-align:left}} td.c{{text-align:center}}
.cathead td{{background:#11141c;color:#9aa3b8;font-weight:700}}
em{{font-style:normal;font-size:10px;padding:1px 5px;border-radius:4px;margin-left:4px}}
em.auto{{background:#16361f;color:#7fe0a0}} em.man{{background:#3a2f12;color:#e7c97a}}
.warn{{font-size:12px;color:#e7c97a;margin-top:8px}}
.foot{{font-size:11px;color:#6b7390;margin-top:14px;line-height:1.6}}
a{{color:#7fa8ff}}
</style></head><body>
<h1>🇰🇷 韩国去杠杆做空择时评分卡</h1>
<div class="sub">KOSPI 200 Put 建仓时机表 · 右侧交易 · 不抄顶　|　数据日 {esc(ddate)}　刷新于 {esc(now)}</div>

<div class="sec"><div class="total">
 <div><div class="bignum" style="color:{r['bcolor']}">{r['total']:.1f}</div></div>
 <div class="statusbox" style="background:{r['bcolor']}">{esc(r['short'])}</div>
</div>
<div class="full">{esc(r['full'])}</div>
<div class="right" style="color:{'#ff9a9a' if not r['trig']['T4'] else '#7fe0a0'}">{esc(r['rightside'])}</div>
</div>

<div class="sec"><div class="sechd">📌 今日动作</div><div class="action">{esc(r['action'])}</div></div>

<div class="sec"><div class="sechd">🎯 第一笔 PUT 触发器</div>{trig_html}
<div class="trigtot">{r['trig_n']} / 5</div></div>

<div class="sec"><div class="sechd">⚠️ IV 闸门（VKOSPI 越高 put 越贵）</div>
<div class="ivbox" style="background:{r['iv_color']}"><b>{esc(r['iv_label'])}　VKOSPI {esc(state.get('vkospi','—'))}（{esc(vk_src)}）　仓位×{r['iv_factor']}</b>
<p>{esc(r['iv_text'])}</p></div></div>

<div class="sec"><div class="sechd">五大类别</div>{cat_html}
<div class="warn">数据置信度 {int(r['conf']*100)}%　·　{esc(man_warn)}</div></div>

<div class="sec"><div class="sechd">📡 自动抓取数据（FinanceDataReader）</div>
<div class="cards">{cards_html}</div></div>

<div class="sec"><div class="sechd">指标明细（自动=实时抓取，手填=判断/无免费源）</div>
<table><tr><th>指标</th><th class="c">权重</th><th class="c">分</th><th class="c">贡献</th></tr>{ind_html}</table></div>

<div class="sec"><div class="sechd">近期历史</div>
<table><tr><th>日期</th><th class="c">KOSPI</th><th class="c">%</th><th class="c">Hynix</th><th class="c">跌占</th><th class="c">总分</th><th class="c">状态</th></tr>{hist_html}</table></div>

<div class="foot">
核心原则：不因为贵就空；等「外资卖 + 国内接不动 + 杠杆反噬 + K200破位反抽失败」四共振，右侧不抄顶。<br>
手填项请在仓库 <b>state.json</b> 更新（VKOSPI、外资/国内状态、K200 状态、判断类打分）；改后推送即自动重算。<br>
⚠️ 本表为个人择时辅助，非投资建议、非预测；期权可能归零，控制仓位与到期。数据每交易日韩股收盘后(15:42 KST)自动刷新。
</div></body></html>"""

# ---------------- 主流程 ----------------
def main():
    with open(P("state.json"), encoding="utf-8") as f:
        state = json.load(f)
    m = fetch_market()
    if "kospi" not in m:
        print("[fatal] 行情抓取失败，保留上次页面", file=sys.stderr); sys.exit(1)
    vk_auto = fetch_vkospi()
    if vk_auto is not None:
        state["vkospi"] = vk_auto; state["_vk_src"] = "auto"
    else:
        state["_vk_src"] = "sticky"
    r = compute(state, m)
    hist_tail = append_history(m, r, state) or []
    htmls = render(m, r, state, hist_tail)
    with open(P("index.html"), "w", encoding="utf-8") as f:
        f.write(htmls)
    status = dict(date=m.get("date"), refreshed=datetime.now(KST).isoformat(timespec="minutes"),
                  total=r["total"], status=r["short"], triggers=f'{r["trig_n"]}/5',
                  gate_first=r["gate_first"], conf=r["conf"], categories=r["cat"],
                  kospi=m.get("kospi"), hynix=m.get("hynix"), concentration=m.get("concentration"),
                  vkospi=state.get("vkospi"), vkospi_src=state.get("_vk_src"))
    with open(P("status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    print(f"OK 数据日={m.get('date')} 总分={r['total']} 状态={r['short']} 触发={r['trig_n']}/5 "
          f"置信度={r['conf']} 集中度={m.get('concentration')} VKOSPI={state.get('vkospi')}({state.get('_vk_src')})")

if __name__ == "__main__":
    main()
