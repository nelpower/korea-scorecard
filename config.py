# -*- coding: utf-8 -*-
"""
韩国去杠杆做空择时评分卡 — 评分配置（与 xlsx 版方法学一致）
- 每个指标: key, 类别, 权重, 名称, 方向(正向/逆向), 来源(auto/manual)
- auto 指标由当日抓取的数据按下方规则算分; manual 指标从 state.json 读取(judgment 或无免费数据源)
- 改框架/权重/阈值只改这一个文件
"""

# ---------- 自动打分规则(数据 -> 0~5) ----------
def _nn(x):
    """None/NaN 守卫。NaN 对所有比较返回 False,会穿透 if 阶梯落到最后的兜底分
    (例: score_us_ai(NaN) 曾静默返回满分 5.0)——统一挡在每个打分函数门口。"""
    return x is None or (isinstance(x, float) and x != x)

def score_usdkrw(rate, mom_pct=None):
    if _nn(rate): return None
    if rate >= 1520: s = 5.0
    elif rate >= 1500: s = 4.5
    elif rate >= 1480: s = 4.0
    elif rate >= 1450: s = 3.5
    elif rate >= 1400: s = 2.5
    else: s = 1.5
    if mom_pct is not None and mom_pct >= 2.0:  # 月跌(韩元走弱)>=2% 共振加成
        s = min(5.0, s + 0.5)
    return s

def score_concentration(pct):           # 双雄市值 / KOSPI 总市值
    if _nn(pct): return None
    if pct >= 0.50: return 5.0
    if pct >= 0.46: return 4.5
    if pct >= 0.42: return 4.0
    if pct >= 0.38: return 3.5
    if pct >= 0.34: return 3.0
    return 2.0

def score_breadth(decl_frac, index_up):  # 跌家数占比 + 指数涨而广度差=背离
    if _nn(decl_frac): return None
    if decl_frac >= 0.70: s = 5.0
    elif decl_frac >= 0.60: s = 4.5
    elif decl_frac >= 0.55: s = 4.0
    elif decl_frac >= 0.50: s = 3.5
    elif decl_frac >= 0.45: s = 3.0
    else: s = 2.0
    if index_up and decl_frac >= 0.55:    # 指数涨但多数跌=典型顶部背离
        s = min(5.0, s + 0.5)
    return s

def score_hynix_zone(price, lower=2450000, upper=2500000):
    if _nn(price): return None
    if not (0 < lower < upper): lower, upper = 2450000, 2500000  # state 手滑(lower=0/倒挂)回落默认,防除零

    if price >= upper: return 5.0
    if price >= lower: return 4.5
    gap = (lower - price) / lower
    if gap <= 0.03: return 4.0
    if gap <= 0.08: return 3.5
    if gap <= 0.15: return 3.0
    return 2.0

def score_divergence(ret_a, ret_b):       # 双雄区间收益分化
    if _nn(ret_a) or _nn(ret_b): return None
    d = abs(ret_a - ret_b)
    if d >= 0.15: return 4.5
    if d >= 0.10: return 4.0
    if d >= 0.06: return 3.5
    if d >= 0.03: return 3.0
    return 2.5

def score_vkospi(v):                       # 来自 state(手填的一个数)
    if _nn(v): return None
    if v >= 60: return 5.0
    if v >= 45: return 4.5
    if v >= 40: return 4.0
    if v >= 30: return 3.0
    if v >= 25: return 2.0
    return 1.0

def score_foreign(rows):                   # Naver 投资者动向: 外资净卖强度/连续性 -> (分, flow_status)
    if not rows: return None, None
    fl = [r["foreign"] for r in rows][:20]  # 억원, 最新在前, 负=净卖; 定窗20日(原窗口隐式=页面行数,语义漂移)
    if _nn(fl[0]): return None, None
    latest, cum = fl[0], sum(v for v in fl if not _nn(v))
    streak = 0
    for v in fl:
        if not _nn(v) and v < 0: streak += 1
        else: break
    if latest >= 0:
        return (2.0, "净买/回补" if latest > 0 else "中性")
    if streak >= 10 or cum <= -60000: return (5.0, "连续净卖/创纪录")
    if streak >= 5:  return (4.5, "连续净卖/创纪录")
    if streak >= 3:  return (4.0, "温和净卖")
    return (3.5, "温和净卖")

def score_domestic(rows):                  # 散户接盘 vs 外资+机构卖压 -> (分, domestic_status)
    if not rows: return None, None
    indiv, foreign = rows[0]["indiv"], rows[0]["foreign"]
    inst = rows[0].get("inst", 0)
    if _nn(indiv) or _nn(foreign): return None, None
    if _nn(inst): inst = 0
    if indiv <= 0:                         # 散户转卖 = 买盘枯竭
        return (4.5, "接不动/枯竭")
    sell = max(0, -foreign) + max(0, -inst)        # 外资+机构净卖合计(机构也卖=卖方扩散)
    both = foreign < 0 and inst < 0                # 外资与机构同时卖
    if sell > 20000:                       # 大举抛压(>2万亿)时看散户独扛吸收比
        ratio = indiv / sell
        if ratio < 0.60: return (4.5, "接不动/枯竭")
        if ratio < 0.85: return (4.2, "边际减弱")   # 接不平
        if both or ratio < 1.15: return (3.8, "边际减弱")  # 机构也卖/仅刚好接平=吃力
        return (3.2, "正常")                          # 轻松接住(仍脆弱杠杆接盘)
    return (3.0, "正常")

def score_us_ai(avg5d):                    # 美股AI龙头(NVDA/MU/AVGO)5日均涨幅: 越弱越危险
    if _nn(avg5d): return None             # NaN 曾穿透比较链落到 return 5.0 满分(2026-06-10 实锤)
    if avg5d > 5:  return 2.0
    if avg5d > 0:  return 3.0
    if avg5d > -5: return 4.0
    return 5.0

def score_upshadow(upsh, volr):            # 放量上影: 上影占比 × 量比
    if _nn(upsh): return None
    s = 4.0 if upsh >= 0.5 else 3.0 if upsh >= 0.3 else 2.0
    if not _nn(volr) and volr and volr >= 1.3: s = min(5.0, s + 0.5)
    return s

def score_margin_l1(mg):                    # KOFIA 全市场融资余额(level+日环比) -> 0-5
    # mg = {"total_tn","change_tn",...} 或 None。38tn≈历史纪录区。
    if not mg or _nn(mg.get("total_tn")): return None
    t, ch = mg["total_tn"], mg.get("change_tn")
    base = 5 if t >= 39 else 4 if t >= 37.5 else 3 if t >= 35 else 2 if t >= 30 else 1
    if _nn(ch): return base
    if t >= 37.0:                           # 已在纪录区
        if ch >= 0.25: base += 1            # 还在猛升=狂热
        elif ch <= -0.30: base = max(base, 5)  # 从极值回落=去杠杆启动,顶格(非'安全')
    elif ch >= 0.40: base += 1              # 非纪录区快速堆积
    return max(0, min(5, base))

def score_credit_l3(biz):   # 미수금대비 반대매매 비중% (KOFIA 060BO TMPV7) — 强平点火温度计
    if _nn(biz): return None
    if biz >= 10: return 5.0     # 螺旋点火
    if biz >= 7:  return 4.0     # 强平潮
    if biz >= 4:  return 3.0
    if biz >= 2:  return 2.0
    if biz >= 1:  return 1.5
    return 1.0                   # melt-up中无强平

# ---------- 30 指标定义(择时表 v3:降backdrop·升trigger·加供给/债信用FX轴) ----------
# kind: 'auto' 用 auto_fn 算; 'manual' 从 state['manual'][key] 读
INDICATORS = [
 # 杠杆/强平 (22) — backdrop降权, 强平点火L3升权并改自动
 dict(key="L1", cat="杠杆/强平", w=4, name="总押杠杆 level+change(拐头=去杠杆)", dir="正向", kind="auto", auto="margin_l1", note="KOFIA 070BO(T+1);权重6→4,拐头向下才是去杠杆信号", src="S2"),
 dict(key="L2", cat="杠杆/强平", w=4, name="Samsung+Hynix 融资集中度", dir="正向", kind="manual", note="近恒定backdrop(降权6→4)", src="S2"),
 dict(key="L3", cat="杠杆/强平", w=7, name="강제반대매매 비중(KOFIA 060BO·头号点火温度计)", dir="正向", kind="auto", auto="credit_l3", note="🆕060BO自动:비중≥7%=强平潮≥10%=螺旋;权重5→7", src="S3"),
 dict(key="L4", cat="杠杆/强平", w=3, name="신용융자 이용률/券商额度收紧", dir="正向", kind="manual", note="多券商停신용=卖方抽梯(news)", src="S3"),
 dict(key="L5", cat="杠杆/强平", w=2, name="杠杆ETF净流+AUM净赎回拐点", dir="正向", kind="manual", note="AUM由申转赎=认输(Naver etfItemList可自动化)", src="S6"),
 dict(key="L6", cat="杠杆/强平", w=2, name="暴跌后再杠杆速度", dir="正向", kind="manual", note="32tn→38tn<90日", src="S8"),
 # 资金结构 (19)
 dict(key="F1", cat="资金结构", w=5, name="外资净卖强度/连续性+换手反转", dir="正向", kind="auto", auto="foreign", note="Naver投资者动向(억원);买卖换手=tactical收割", src="S4"),
 dict(key="F2", cat="资金结构", w=3, name="外资卖盘集中 Samsung/Hynix", dir="正向", kind="manual", note="集中双雄(降权4→3)", src="S4"),
 dict(key="F3", cat="资金结构", w=4, name="国内接盘质量+예탁금背离", dir="正向", kind="auto", auto="domestic", note="散户靠融资接盘;예탁금↓而margin↑=枯竭背离", src="S4"),
 dict(key="F4", cat="资金结构", w=3, name="韩元与股票共振走弱(USD/KRW)", dir="正向", kind="auto", auto="usdkrw", note="~1,512极弱", src="S9"),
 dict(key="F5", cat="资金结构", w=2, name="外资借券做空/空头禁令状态", dir="正向", kind="manual", note="解禁延续", src="S12"),
 dict(key="F6", cat="资金结构", w=2, name="NPS/养老金政策托底(反身衰减)", dir="逆向", kind="manual", note="超配3.7pp=未来卖方(降权3→2)", src="S13"),
 # 市场结构 (14) — backdrop降权 + 新增供给轴
 dict(key="M1", cat="市场结构", w=3, name="Samsung+Hynix 占 KOSPI 市值", dir="正向", kind="auto", auto="concentration", note="近恒定backdrop(降权5→3)", src="S5"),
 dict(key="M2", cat="市场结构", w=2, name="科技/半导体盈利贡献集中度", dir="正向", kind="manual", note="慢变量backdrop(降权3→2)", src="S6"),
 dict(key="M3", cat="市场结构", w=4, name="广度恶化(跌>涨/加权背离)", dir="正向", kind="auto", auto="breadth", note="加权-等权背离(降权5→4)", src="S6"),
 dict(key="M4", cat="市场结构", w=1, name="被动盘/程序化交易拥挤", dir="正向", kind="manual", note="被动盘接外资(降权2→1)", src="S6"),
 dict(key="SU1", cat="市场结构", w=4, name="🆕供给/稀释冲击(lockup解禁+净供给-回购)", dir="正向", kind="manual", note="#32轴:lockup/유상증자/block vs 回购소각;DART/SEIBRO事件流(现回购소각=负供给顺风)", src="SU"),
 # 价格行为 (19)
 dict(key="P1", cat="价格行为", w=4, name="Hynix 进入 245w-250w 清仓区", dir="正向", kind="auto", auto="hynix_zone", note="清仓价245w-250w", src="S5"),
 dict(key="P2", cat="价格行为", w=5, name="好消息不涨/冲高回落", dir="正向", kind="manual", note="利好不再推涨(右侧关键)", src="S1"),
 dict(key="P3", cat="价格行为", w=2, name="Samsung 与 Hynix 分化", dir="正向", kind="auto", auto="divergence", note="涨幅分化(降权3→2)", src="S5"),
 dict(key="P4", cat="价格行为", w=5, name="K200 破位并反抽失败【右侧总闸】", dir="正向", kind="manual", note="破位+反抽失败才亮", src="S1"),
 dict(key="P5", cat="价格行为", w=3, name="放量上影/买盘效率下降", dir="正向", kind="auto", auto="upshadow", note="上影占比×量比(升权2→3)", src="S7"),
 # 波动/宏观/AI外溢/信用 (26) — 加债/信用/FX trigger, trim慢AI
 dict(key="V1", cat="波动/宏观/AI外溢", w=3, name="VKOSPI 高位/不回落", dir="正向", kind="auto", auto="vkospi", note="降权4→3", src="S7"),
 dict(key="V2", cat="波动/宏观/AI外溢", w=2, name="put/call skew 与 K200 对冲成本", dir="正向", kind="manual", note="put贵负carry(降权3→2)", src="S7"),
 dict(key="V3", cat="波动/宏观/AI外溢", w=3, name="ELS/ELB knock-in 反身性", dir="正向", kind="manual", note="距in 40-60%(降权4→3)", src="S_ELS"),
 dict(key="V4", cat="波动/宏观/AI外溢", w=4, name="DRAM/DDR5 现货价拐点(vs HBM背离·引擎裂缝)", dir="正向", kind="manual", note="引擎裂缝最领先扳机(升权3→4;Micron 6/24)", src="S_DRAM"),
 dict(key="V5", cat="波动/宏观/AI外溢", w=2, name="美股AI龙头+Micron/NVDA外溢", dir="正向", kind="auto", auto="us_ai", note="NVDA/MU/AVGO 5日动能(降权3→2)", src="S_DRAM"),
 dict(key="V6", cat="波动/宏观/AI外溢", w=2, name="AI capex/ROI 叙事反转", dir="正向", kind="manual", note="最慢但最致命(降权3→2)", src="S_DRAM"),
 dict(key="CR1", cat="波动/宏观/AI外溢", w=5, name="🆕회사채信用利差(BBB−국고)走阔+KTB三杀", dir="正向", kind="manual", note="债/信用trigger:利差领先股市最后一跌;ECOS免费日频(可自动化)", src="CR"),
 dict(key="CR2", cat="波动/宏观/AI外溢", w=5, name="🆕FX swap point/cross-ccy basis(美元荒)+主权CDS", dir="正向", kind="manual", note="外资出逃-via-韩元判别器;basis藏不住美元饥渴(2008/2020先崩);ECOS swap point免费", src="CR"),
]

CATS = ["杠杆/强平", "资金结构", "市场结构", "价格行为", "波动/宏观/AI外溢"]

# ---------- 动作带 / gating / IV 阈值 ----------
def band(total):
    if _nn(total): return ("数据异常", "总分非数 — 检查数据源/state.json", "#9E9E9E")
    if total < 35: return ("只记录", "0–35 低风险 / 只记录", "#63BE7B")
    if total < 55: return ("监控", "35–55 中风险 / 重点监控", "#A9D08E")
    if total < 70: return ("观察仓", "55–70 高风险 / 观察仓区", "#FFC000")
    if total < 82: return ("第一笔", "70–82 第一笔 put 条件接近", "#ED7D31")
    return ("主仓区", "82–100 去杠杆确认 / 主仓区", "#C00000")

def iv_gate(vkospi):
    # IV 未知 ≠ IV 正常:未知时按最保守档(×0.4)处理,数据恢复前不放大仓位(原版给 ×1.0,方向危险)
    if _nn(vkospi): return ("⚪ IV未知", "VKOSPI 缺失/超龄 → 按最保守档:只用 put 价差/缩仓×0.4,数据恢复前不加仓", 0.4, "#9E9E9E")
    if vkospi >= 60: return ("🔴 过贵", "禁裸 put(负EV)。只用 put 价差/缩仓×0.4，或等回落<40", 0.4, "#7030A0")
    if vkospi >= 45: return ("🔴 过贵", "优先 put 价差，仓位×0.6", 0.6, "#C00000")
    if vkospi >= 28: return ("🟡 偏贵", "裸 put 可小仓，或近月价差(×0.8)", 0.8, "#FFC000")
    return ("🟢 可裸买", "IV 正常，裸 put 可接受", 1.0, "#63BE7B")

GATE_PRICE_CAT_MIN = 60     # 价格行为类别分门槛
GATE_TOTAL_MIN = 70
GATE_ADD_TOTAL_MIN = 82
FRESH_DAYS = 5              # 手动核心指标新鲜度阈值(自然日)
