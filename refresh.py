# -*- coding: utf-8 -*-
"""
每交易日韩股收盘后自动跑：抓数据 -> 算分 -> 追加历史 -> 生成 index.html + status.json
本地: python refresh.py    云端: GitHub Actions cron
"""
import os, json, csv, sys, html, math, re
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

def fetch_foreign_flows(bizdate):
    """Naver 投资者动向(KOSPI, 단위:억원, 最新在前)。bizdate='YYYYMMDD'。"""
    import requests, re
    ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
    url = f"https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate={bizdate}&sosok=01"
    r = requests.get(url, headers=ua, timeout=20); r.encoding = "euc-kr"
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip().replace(",", "") for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        cells = [c for c in cells if c != ""]
        # 数值形校验:防空单元格挤掉后列序左移(外资值滑进散户槽位静默错列)
        if (len(cells) >= 4 and re.match(r"\d{2}\.\d{2}\.\d{2}$", cells[0])
                and all(re.fullmatch(r"-?\d+(\.\d+)?", c) for c in cells[1:4])):
            try: out.append({"date": cells[0], "indiv": float(cells[1]), "foreign": float(cells[2]), "inst": float(cells[3])})
            except Exception: pass
    return out

def fetch_us_ai():
    """美股 AI 龙头 NVDA/MU/AVGO 近 5 交易日均涨幅(%)。NaN close 会让均值变 NaN
    并绕过 None 检查(round(NaN)=NaN),下游打分函数曾因此给出满分——逐项过滤。"""
    import yfinance as yf
    rets = []
    for tk in ("NVDA", "MU", "AVGO"):
        h = yf.Ticker(tk).history(period="10d")["Close"].dropna().tail(6)  # ~5个交易日(7d 跨假日常只剩4日)
        if len(h) >= 2:
            ret = (float(h.iloc[-1]) / float(h.iloc[0]) - 1) * 100
            if math.isfinite(ret):
                rets.append(ret)
    return round(sum(rets) / len(rets), 1) if len(rets) >= 2 else None  # 至少2只,防单票"平均"

def fetch_margin(timeout=30, proxy_key=None):
    """KOFIA FreeSIS 신용거래융자 잔고(全市场, T+1)。返回最新交易日 + 日环比(万亿)。失败返回 None。
    经第4 agent 独立审计:冷 POST 即 200(云端可直连), 5/29=38.0227tn 精确复现。"""
    import requests
    from datetime import date, timedelta
    URL = "https://freesis.kofia.or.kr/meta/getMetaDataList.do"
    today = date.today()
    payload = {"dmSearch": {"tmpV40": "1000000", "tmpV41": "1", "tmpV1": "D",
                            "tmpV45": (today - timedelta(days=12)).strftime("%Y%m%d"),
                            "tmpV46": (today + timedelta(days=1)).strftime("%Y%m%d"),
                            "OBJ_NM": "STATSCU0100000070BO"}}
    hdr = {"Content-Type": "application/json", "Accept": "application/json",
           "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
    def post(use_proxy):
        if use_proxy:
            u = f"https://api.scraperapi.com/?api_key={proxy_key}&url={URL}"
            return requests.post(u, json=payload, headers={"Content-Type": "application/json"}, timeout=max(timeout, 70)).json()
        return requests.post(URL, json=payload, headers=hdr, timeout=timeout).json()
    def parse(j):
        ds1 = j.get("ds1")
        if not isinstance(ds1, list) or not ds1: return None
        rows = sorted(ds1, key=lambda x: str(x.get("TMPV1", "")), reverse=True)
        def tn(r, k): return round(float(str(r[k]).replace(",", "")) / 1e6, 4)
        top = rows[0]; raw = str(top["TMPV1"])
        out = {"date": f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}", "total_tn": tn(top, "TMPV2"),
               "kospi_tn": tn(top, "TMPV3"), "kosdaq_tn": tn(top, "TMPV4"),
               "change_tn": round(tn(top, "TMPV2") - tn(rows[1], "TMPV2"), 4) if len(rows) > 1 else None}
        return out
    for use_proxy in ([False, True] if proxy_key else [False]):
        try:
            res = parse(post(use_proxy))
            if res:
                res["source"] = "kofia-proxy" if use_proxy else "kofia"
                return res
        except Exception as e:
            print(f"[warn] margin {'proxy' if use_proxy else 'direct'}: {type(e).__name__}: {str(e)[:60]}", file=sys.stderr)
    return None

def fetch_credit(timeout=30, proxy_key=None):
    """KOFIA 060BO 증시자금(T+1): 미수금대비 반대매매 비중%(TMPV7=强平点火温度计) + 강평额(TMPV6→억) + 예탁금(TMPV3→조)。返回最新交易日 dict 或 None。"""
    import requests
    from datetime import date, timedelta
    URL = "https://freesis.kofia.or.kr/meta/getMetaDataList.do"
    today = date.today()
    payload = {"dmSearch": {"tmpV40": "1000000", "tmpV41": "1", "tmpV1": "D",
                            "tmpV45": (today - timedelta(days=12)).strftime("%Y%m%d"),
                            "tmpV46": (today + timedelta(days=1)).strftime("%Y%m%d"),
                            "OBJ_NM": "STATSCU0100000060BO"}}
    hdr = {"Content-Type": "application/json", "Accept": "application/json",
           "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
    def post(use_proxy):
        if use_proxy:
            u = f"https://api.scraperapi.com/?api_key={proxy_key}&url={URL}"
            return requests.post(u, json=payload, headers={"Content-Type": "application/json"}, timeout=max(timeout, 70)).json()
        return requests.post(URL, json=payload, headers=hdr, timeout=timeout).json()
    def num(top, k):
        try: return float(str(top[k]).replace(",", ""))
        except Exception: return None
    def parse(j):
        ds1 = j.get("ds1")
        if not isinstance(ds1, list) or not ds1: return None
        top = sorted(ds1, key=lambda x: str(x.get("TMPV1", "")), reverse=True)[0]
        raw = str(top["TMPV1"]); biz = num(top, "TMPV7"); ban = num(top, "TMPV6"); yet = num(top, "TMPV2")  # TMPV2=投资者预托金~130万亿(=新闻口径);旧误用TMPV3(57)
        return {"date": f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}", "biz": biz,
                "banamt_eok": round(ban / 1e2) if ban is not None else None,
                "yetak_tn": round(yet / 1e6, 2) if yet is not None else None}
    for use_proxy in ([False, True] if proxy_key else [False]):
        try:
            res = parse(post(use_proxy))
            if res and res.get("biz") is not None:
                res["source"] = "kofia-proxy" if use_proxy else "kofia"
                return res
        except Exception as e:
            print(f"[warn] credit {'proxy' if use_proxy else 'direct'}: {type(e).__name__}: {str(e)[:60]}", file=sys.stderr)
    return None

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
    # ── yfinance 交叉源：FDR 日线收盘常滞后到当晚,yfinance 多半已有当日收盘;谁日期新用谁 ──
    def yf_close(tk):
        import yfinance as yf
        h = yf.Ticker(tk).history(period="6d").dropna(subset=["Close"])
        if not len(h): return None
        return (float(h["Close"].iloc[-1]), float(h["Close"].iloc[-2]) if len(h) > 1 else None, h.index[-1].date().isoformat())
    yk = safe(lambda: yf_close("^KS11"), "yf KOSPI")
    if yk and yk[2] > m.get("date", ""):       # yfinance 比 FDR 新 → 覆盖日期+收盘
        m["kospi"], m["kospi_prev"], m["date"] = yk
        m["_price_src"] = "yfinance"
        for tk, key in [("^KS200", "k200"), ("000660.KS", "hynix"), ("005930.KS", "samsung")]:
            v = safe(lambda tk=tk: yf_close(tk), f"yf {key}")
            if v and v[2] == yk[2]:            # 必须与 KOSPI 同日才接受(防部分 ticker 限流时新旧混拼)
                m[key] = v[0]
            else:                              # 留下的是 FDR T-1 旧值:标记并在页面亮警告
                m.setdefault("_stale_syms", []).append(key)
                print(f"[warn] yf {key}: 无当日({yk[2]})数据,沿用 FDR 旧值", file=sys.stderr)
        v = safe(lambda: yf_close("KRW=X"), "yf USDKRW")
        if v: m["usdkrw"] = v[0]               # FX 连续交易,小时级时差可接受,不做同日硬校验
    # 市值集中度 + 广度
    lst = safe(lambda: fdr.StockListing("KOSPI"), "Listing")
    if lst is not None and "Marcap" in lst.columns:
        try:
            total = float(lst["Marcap"].sum())
            ssm = float(lst.loc[lst["Code"] == "005930", "Marcap"].iloc[0])
            hxm = float(lst.loc[lst["Code"] == "000660", "Marcap"].iloc[0])
            conc = (ssm + hxm) / total
            if math.isfinite(conc) and 0 < conc < 1:   # Marcap 单元格缺值→NaN 会污染打分/CSV/status.json
                m["concentration"] = conc
                m["mcap_samsung_tn"] = ssm / 1e12
                m["mcap_hynix_tn"] = hxm / 1e12
            else:
                print(f"[warn] concentration 非有限值({conc}),弃用走 sticky", file=sys.stderr)
        except Exception as e:
            print(f"[warn] concentration: {e}", file=sys.stderr)
        try:
            cr = lst["ChagesRatio"].dropna()
            adv = int((cr > 0).sum()); dec = int((cr < 0).sum())
            m["adv"], m["dec"] = adv, dec
            if adv + dec > 0: m["decl_frac"] = dec / (adv + dec)
        except Exception as e:
            print(f"[warn] breadth: {e}", file=sys.stderr)
    # 集中度/广度抓取失败 → 沿用历史最近值(避免分数因单次失败塌陷)
    if "concentration" not in m:
        lv = last_hist("concentration")
        if lv is not None: m["concentration"], m["_conc_stale"] = lv, True
    if "decl_frac" not in m:
        lv = last_hist("decl_frac")
        if lv is not None: m["decl_frac"], m["_breadth_stale"] = lv, True
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
    # 外资/散户资金流(Naver), 美股AI动能, 海力士放量上影
    if m.get("date"):
        fl = safe(lambda: fetch_foreign_flows(m["date"].replace("-", "")), "Foreign flows", [])
        if fl:
            want = m["date"][2:].replace("-", ".")   # '2026-06-10' -> '26.06.10'
            if fl[0].get("date") != want:            # Naver 未出今日数据:弃用,防昨日资金流冒充今日
                print(f"[warn] flows 最新行 {fl[0].get('date')} != 行情日 {want},弃用", file=sys.stderr)
                fl = []
        m["flows"] = fl
    m["us_ai"] = safe(fetch_us_ai, "US AI")
    m["margin"] = safe(lambda: fetch_margin(proxy_key=os.environ.get("SCRAPERAPI_KEY")), "Margin", None)
    m["credit"] = safe(lambda: fetch_credit(proxy_key=os.environ.get("SCRAPERAPI_KEY")), "Credit(060BO)", None)
    def hynix_ohlc():
        df = fdr.DataReader("000660", start).dropna()
        last = df.iloc[-1]; rng = float(last["High"] - last["Low"])
        upsh = (float(last["High"] - last["Close"]) / rng) if rng else 0.0
        volr = float(last["Volume"]) / float(df["Volume"].tail(20).mean())
        return round(upsh, 2), round(volr, 2)
    oh = safe(hynix_ohlc, "Hynix OHLC")
    if oh: m["upshadow"], m["volratio"] = oh
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
    if a == "foreign":       return C.score_foreign(m.get("flows") or [])[0]
    if a == "domestic":      return C.score_domestic(m.get("flows") or [])[0]
    if a == "us_ai":         return C.score_us_ai(m.get("us_ai"))
    if a == "upshadow":      return C.score_upshadow(m.get("upshadow"), m.get("volratio"))
    if a == "margin_l1":     return C.score_margin_l1(m.get("margin"))
    if a == "credit_l3":     return C.score_credit_l3((m.get("credit") or {}).get("biz"))
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
            hr = None; e_disp = None
        else:
            hr = ind["w"] * (5 - e) / 5 if ind["dir"] == "逆向" else ind["w"] * e / 5
            e_disp = e
        # h 仅显示用; _hr 未取整参与求和(预取整求和漂移可达 ±0.14,恰在 70/82 闸门边界会翻信号)
        rows.append({**ind, "e": e_disp, "h": round(hr, 2) if hr is not None else 0.0,
                     "_hr": hr if hr is not None else 0.0, "esrc": esrc})
    # 缺数指标(e=None)从分子分母同时剔除,按当日有效权重归一——
    # 否则一次抓取失败=贡献记0但权重仍占分母,总分/类分被静默稀释,可翻转 T3/gate(审计实测 -6 分)
    valid_w = sum(r["w"] for r in rows if r["e"] is not None)
    total = round(sum(r["_hr"] for r in rows) / valid_w * 100, 1) if valid_w else 0.0
    missing = [r["key"] for r in rows if r["e"] is None]
    cat = {}
    for c in C.CATS:
        wsum = sum(r["w"] for r in rows if r["cat"] == c and r["e"] is not None)
        hsum = sum(r["_hr"] for r in rows if r["cat"] == c)
        cat[c] = round(hsum / wsum * 100, 1) if wsum else 0
    # 手动新鲜度(供触发器兜底与置信度共用,先算)
    mu = state.get("manual_update")
    days = None
    if mu:
        try: days = (datetime.now(KST).date() - datetime.fromisoformat(mu).date()).days
        except Exception: days = None
    manual_fresh = (days is not None and days <= C.FRESH_DAYS)
    # 资金流状态(优先用 Naver 投资者动向自动判定; 抓取失败则沿用 state.json 手填串)
    flows = m.get("flows") or []
    flows_live = bool(flows)
    flow_status = (C.score_foreign(flows)[1] if flows else None) or state.get("flow_status", "")
    domestic_status = (C.score_domestic(flows)[1] if flows else None) or state.get("domestic_status", "")
    # 触发器
    k200 = state.get("k200_status", "")
    t = {}
    # T1/T2 兜底串超龄闸:抓取失败且手填串已过期时不得点亮触发器(防旧串永久点亮虚增 trig)
    flow_ok = flows_live or manual_fresh
    t["T1"] = flow_status == "连续净卖/创纪录" and k200 != "创新高/强势" and flow_ok
    t["T2"] = domestic_status in ("边际减弱", "接不动/枯竭") and flow_ok
    t["T3"] = cat["杠杆/强平"] >= 80
    t["T4"] = k200 in ("跌破后反抽失败", "周线破位反抽失败")
    t["T5"] = (m.get("hynix") is not None) and (m["hynix"] >= state.get("hynix_lower", 2450000))
    trig = sum(1 for v in t.values() if v)
    # 置信度: 分母=全部指标数(缺数即扣分)。
    # 原公式分母=非空指标数,manual 新鲜时恒等于 1.0,auto 全挂也显示 100%——失真且 gate 的 conf 闸形同虚设
    n_auto = sum(1 for r in rows if r["esrc"] == "auto" and r["e"] is not None)
    n_manual = sum(1 for r in rows if r["esrc"] == "manual" and r["e"] is not None)
    n_ind = len(C.INDICATORS)
    conf = round((n_auto + (n_manual if manual_fresh else 0)) / n_ind, 2) if n_ind else 0
    # gating
    vk = state.get("vkospi")
    iv_label, iv_text, iv_factor, iv_color = C.iv_gate(vk)
    gate_first = (total >= C.GATE_TOTAL_MIN and cat["价格行为"] >= C.GATE_PRICE_CAT_MIN
                  and trig >= 3 and t["T4"] and conf >= 0.5)
    gate_add = (gate_first and total >= C.GATE_ADD_TOTAL_MIN and trig >= 4
                and cat["杠杆/强平"] >= 70 and conf >= 0.8)   # gate_add 必须是 gate_first 超集(原版缺价格行为类检查)
    short, full, bcolor = C.band(total)
    if total >= 70 and not gate_first:
        short = f"{short}·待右侧"   # ≥82 未放行也要带后缀(原版只标 70-82,主仓区反而缺限定语)
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
                short=short, full=full, bcolor=bcolor, action=action, rightside=rightside,
                flow_status=flow_status, domestic_status=domestic_status,
                flows_live=flows_live, missing=missing)

# ---------------- 历史 ----------------
HIST = P("data_history.csv")
HCOLS = ["date","kospi","kospi_chg","k200","samsung","hynix","usdkrw","concentration","adv","dec","decl_frac","vkospi","total","status"]
def last_hist(col, max_age_days=5):
    """读 data_history.csv 该列最近一个非空值(给集中度/广度做 sticky 兜底)。
    只接受 max_age_days 内的行——否则抓取连挂时旧值永久 sticky(审计实锤:6/5、6/8 行抄 6/4 值)。"""
    try:
        if not os.path.exists(HIST): return None
        today = datetime.now(KST).date()
        with open(HIST, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for r in reversed(rows):
            v = r.get(col, "")
            if v in ("", None): continue
            try:
                age = (today - datetime.fromisoformat(r.get("date", "")).date()).days
            except Exception:
                continue
            if age > max_age_days: return None   # 最近一个非空值已超龄,直接放弃(别继续往更旧翻)
            fv = float(v)
            return fv if math.isfinite(fv) else None
    except Exception:
        pass
    return None
def append_history(m, r, state):
    date = m.get("date")
    if not date: return
    existing = []
    if os.path.exists(HIST):
        with open(HIST, encoding="utf-8") as f:
            existing = list(csv.DictReader(f))
    # 非当日的已有行不改写:节假日/周末重算时 m["date"] 是旧交易日,
    # 用降级数据(flows空/个股stale)覆盖已收盘定格的记录会污染历史
    today = datetime.now(KST).date().isoformat()
    if date != today and any(e.get("date") == date for e in existing):
        return existing[-30:]
    # sticky/stale 值不写 CSV:它们本来就是从 CSV 读出来的旧值,写回去=旧值被当新观测,
    # 抓取连挂时永不过期(审计实锤:6/5、6/8 行的 concentration/decl_frac 抄 6/4)
    stale = set(m.get("_stale_syms", []))
    row = {"date": date, "kospi": m.get("kospi"), "kospi_chg": round(m["kospi_chg"],2) if "kospi_chg" in m else "",
           "k200": None if "k200" in stale else m.get("k200"),
           "samsung": None if "samsung" in stale else m.get("samsung"),
           "hynix": None if "hynix" in stale else m.get("hynix"),
           "usdkrw": m.get("usdkrw"),
           "concentration": "" if m.get("_conc_stale") else (round(m["concentration"],4) if "concentration" in m else ""),
           "adv": m.get("adv"), "dec": m.get("dec"),
           "decl_frac": "" if m.get("_breadth_stale") else (round(m["decl_frac"],3) if "decl_frac" in m else ""),
           "vkospi": state.get("vkospi") if (state.get("_vk_src") == "auto" or r.get("manual_fresh")) else "",
           "total": r["total"], "status": r["short"]}
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
    vk_disp = state["vkospi"] if state.get("vkospi") is not None else "—"   # None 会被 get 默认值漏过,显示成"None"
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
    flows = m.get("flows") or []
    foreign_str = f'{flows[0]["foreign"]:+,.0f}' if flows else "—"
    indiv_str = f'{flows[0]["indiv"]:+,.0f}' if flows else "—"
    usai_str = f'{m["us_ai"]:+.1f}%' if m.get("us_ai") is not None else "—"
    upsh_str = (f'{m["upshadow"]:.2f}×{m.get("volratio","—")}' if m.get("upshadow") is not None else "—")
    mg = m.get("margin")
    margin_str = ((f'{mg["total_tn"]:.1f}tn' + (f' {mg["change_tn"]:+.2f}' if mg.get("change_tn") is not None else '')) if mg else "—")
    margin_lbl = (f'融资余额({mg["date"][5:]},T+1)' if mg else "融资余额(신용)")
    cr = m.get("credit")
    credit_str = (f'{cr["biz"]:.1f}% / {cr["banamt_eok"]:,.0f}亿' if cr and cr.get("biz") is not None else "—")
    yetak_str = (f'{cr["yetak_tn"]:.1f}tn' if cr and cr.get("yetak_tn") is not None else "—")
    auto_cards = [
        ("KOSPI", f'{fmt(m.get("kospi"),0)}' + (f' <small>({m["kospi_chg"]:+.2f}%)</small>' if "kospi_chg" in m else "")),
        ("KOSPI200", fmt(m.get("k200"),1)),
        ("SK Hynix", fmt(m.get("hynix"),0)),
        ("Samsung", fmt(m.get("samsung"),0)),
        ("USD/KRW", fmt(m.get("usdkrw"),1)),
        ("双雄市值占比", f'{m["concentration"]*100:.1f}%' if "concentration" in m else "—"),
        ("广度(涨/跌)", f'{m.get("adv","—")}/{m.get("dec","—")}'),
        (f"VKOSPI({vk_src})", vk_disp),
        ("外资净流(亿won)", foreign_str),
        ("散户净流(亿won)", indiv_str),
        ("美股AI 5日", usai_str),
        ("海力士上影×量比", upsh_str),
        (margin_lbl, margin_str),
        ("강평비중/额(060BO)", credit_str),
        ("예탁금(散户弹药)", yetak_str),
    ]
    cards_html = "".join(f'<div class="card"><span>{esc(k)}</span><b>{v}</b></div>' for k,v in auto_cards)
    # ①② 的判定来源要可见:自动抓到 vs 兜底手填 vs 手填超龄被禁,三种是完全不同的可信度
    flow_src = ("自动判定" if r.get("flows_live")
                else "⚠️ 抓取失败·沿用手填" if r.get("manual_fresh")
                else "⚠️ 抓取失败且手填超龄 → ①②已禁用")
    flow_note = f'资金流({flow_src}，驱动①②)：外资 <b>{esc(r.get("flow_status","—"))}</b> ｜ 国内 <b>{esc(r.get("domestic_status","—"))}</b>'
    # 数据质量警告横幅(有内容才显示)
    warn_items = list(m.get("_warns") or [])
    if r.get("missing"):
        warn_items.append(f"缺数指标(已从分母剔除,不稀释总分): {', '.join(r['missing'])}")
    if m.get("_stale_syms"):
        warn_items.append(f"个股价非当日(yfinance 缺当日,沿用 FDR T-1): {', '.join(m['_stale_syms'])}")
    if m.get("_conc_stale"): warn_items.append("双雄市值占比为 ≤5 日内旧值(sticky 兜底)")
    if m.get("_breadth_stale"): warn_items.append("广度(跌占比)为 ≤5 日内旧值(sticky 兜底)")
    warn_html = ""
    if warn_items:
        lis = "".join(f"<li>{esc(w)}</li>" for w in warn_items)
        warn_html = (f'<div class="sec" style="border:1px solid #5a4d1a"><div class="sechd">⚠️ 数据质量警告</div>'
                     f'<ul style="font-size:12px;color:#e7c97a;padding-left:18px;line-height:1.7">{lis}</ul></div>')
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
{warn_html}

<div class="sec"><div class="total">
 <div><div class="bignum" style="color:{r['bcolor']}">{r['total']:.1f}</div></div>
 <div class="statusbox" style="background:{r['bcolor']}">{esc(r['short'])}</div>
</div>
<div class="full">{esc(r['full'])}</div>
<div class="right" style="color:{'#ff9a9a' if not r['trig']['T4'] else '#7fe0a0'}">{esc(r['rightside'])}</div>
</div>

<div class="sec"><div class="sechd">📌 今日动作</div><div class="action">{esc(r['action'])}</div></div>

<div class="sec"><div class="sechd">🎯 第一笔 PUT 触发器</div>{trig_html}
<div class="trigtot">{r['trig_n']} / 5</div>
<div style="font-size:11px;color:#8b93a7;margin-top:6px">{flow_note}</div></div>

<div class="sec"><div class="sechd">⚠️ IV 闸门（VKOSPI 越高 put 越贵）</div>
<div class="ivbox" style="background:{r['iv_color']}"><b>{esc(r['iv_label'])}　VKOSPI {esc(vk_disp)}（{esc(vk_src)}）　仓位×{r['iv_factor']}</b>
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
⚠️ 本表为个人择时辅助，非投资建议、非预测；期权可能归零，控制仓位与到期。每交易日 15:50 / 18:20 / 20:40 KST 三班自动刷新（免费版 cron 有延迟与跳班，以页首「刷新于」为准）。
</div></body></html>"""

# ---------------- 主流程 ----------------
def validate_state(state):
    """手填 state.json 防御。枚举 typo / 数值越界都是静默改信号的(如 k200_status 尾随空格
    → T4 右侧总闸永不点亮且无任何提示)。除 strip 和数值钳制外不自动改值,问题收进警告横幅。"""
    warns = []
    for key, opts_key in (("k200_status", "_k200_options"),
                          ("flow_status", "_flow_options"),
                          ("domestic_status", "_domestic_options")):
        v = state.get(key)
        if isinstance(v, str):
            s = v.strip()
            if s != v: state[key] = v = s
            opts = state.get(opts_key) or []
            if opts and v and v not in opts:
                warns.append(f"state.{key}=「{v}」不在枚举里(typo?)——按字面匹配的触发器不会点亮")
    man = state.get("manual") or {}
    for k, v in list(man.items()):
        try:
            fv = float(v)
            if not math.isfinite(fv): raise ValueError
        except Exception:
            warns.append(f"manual.{k}=「{v}」非数 → 按缺数处理"); man[k] = None; continue
        cv = min(5.0, max(0.0, fv))
        if cv != fv: warns.append(f"manual.{k}={fv} 超出 [0,5] → 钳制为 {cv}")
        man[k] = cv
    vk = state.get("vkospi")
    if vk is not None:
        try:
            fv = float(vk)
            if not (math.isfinite(fv) and 1 < fv < 300): raise ValueError
            state["vkospi"] = fv
        except Exception:
            warns.append(f"state.vkospi=「{vk}」非法(需 1~300 的数) → 置空,IV 闸门按未知最保守档")
            state["vkospi"] = None
    return warns

def main():
    with open(P("state.json"), encoding="utf-8") as f:
        state = json.load(f)
    warns = validate_state(state)
    m = fetch_market()
    if "kospi" not in m:
        print("[fatal] 行情抓取失败，保留上次页面", file=sys.stderr); sys.exit(1)
    vk_auto = fetch_vkospi()
    if vk_auto is not None:
        state["vkospi"] = vk_auto; state["_vk_src"] = "auto"
    else:
        state["_vk_src"] = "sticky"
        # 沿用的手填 VKOSPI 一旦超龄就不是"IV 数据"而是化石——置空让 iv_gate 走最保守档(×0.4),
        # 否则三周前的 88 还在当今天的 IV 用
        mu = state.get("manual_update")
        try:
            vk_days = (datetime.now(KST).date() - datetime.fromisoformat(mu).date()).days if mu else None
        except Exception:
            vk_days = None
        if state.get("vkospi") is not None and (vk_days is None or vk_days > C.FRESH_DAYS):
            state["vkospi"] = None
            warns.append(f"VKOSPI 自动抓取失败且手填值已超 {C.FRESH_DAYS} 天 → 按 IV 未知最保守档(×0.4)")
    m["_warns"] = warns
    r = compute(state, m)
    # 自动面熔断:11 个自动指标过半失效说明数据链路崩了,这时算出的分是噪音——
    # 宁可保留上次页面(workflow 红灯可见)也不发布误导信号
    n_auto_ok = sum(1 for row in r["rows"] if row["esrc"] == "auto" and row["e"] is not None)
    if n_auto_ok < 6:
        print(f"[fatal] 自动指标仅 {n_auto_ok}/11 有效(<6),数据面崩坏,保留上次页面", file=sys.stderr)
        sys.exit(1)
    hist_tail = append_history(m, r, state) or []
    htmls = render(m, r, state, hist_tail)
    with open(P("index.html"), "w", encoding="utf-8") as f:
        f.write(htmls)
    status = dict(date=m.get("date"), refreshed=datetime.now(KST).isoformat(timespec="minutes"),
                  total=r["total"], status=r["short"], triggers=f'{r["trig_n"]}/5',
                  gate_first=r["gate_first"], gate_add=r["gate_add"], conf=r["conf"], categories=r["cat"],
                  kospi=m.get("kospi"), hynix=m.get("hynix"), concentration=m.get("concentration"),
                  vkospi=state.get("vkospi"), vkospi_src=state.get("_vk_src"),
                  flows_live=r["flows_live"], n_auto=n_auto_ok, missing=r["missing"],
                  stale_syms=m.get("_stale_syms", []), warns=warns)
    with open(P("status.json"), "w", encoding="utf-8") as f:
        # allow_nan=False: NaN 序列化成裸 nan 不是合法 JSON,下游 JSON.parse 直接炸——宁可这里炸红 workflow
        json.dump(status, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(f"OK 数据日={m.get('date')} 总分={r['total']} 状态={r['short']} 触发={r['trig_n']}/5 "
          f"置信度={r['conf']} 集中度={m.get('concentration')} VKOSPI={state.get('vkospi')}({state.get('_vk_src')})")

if __name__ == "__main__":
    main()
