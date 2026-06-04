# -*- coding: utf-8 -*-
"""
韩国去杠杆做空择时评分卡 — 评分配置（与 xlsx 版方法学一致）
- 每个指标: key, 类别, 权重, 名称, 方向(正向/逆向), 来源(auto/manual)
- auto 指标由当日抓取的数据按下方规则算分; manual 指标从 state.json 读取(judgment 或无免费数据源)
- 改框架/权重/阈值只改这一个文件
"""

# ---------- 自动打分规则(数据 -> 0~5) ----------
def score_usdkrw(rate, mom_pct=None):
    if rate is None: return None
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
    if pct is None: return None
    if pct >= 0.50: return 5.0
    if pct >= 0.46: return 4.5
    if pct >= 0.42: return 4.0
    if pct >= 0.38: return 3.5
    if pct >= 0.34: return 3.0
    return 2.0

def score_breadth(decl_frac, index_up):  # 跌家数占比 + 指数涨而广度差=背离
    if decl_frac is None: return None
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
    if price is None: return None
    if price >= upper: return 5.0
    if price >= lower: return 4.5
    gap = (lower - price) / lower
    if gap <= 0.03: return 4.0
    if gap <= 0.08: return 3.5
    if gap <= 0.15: return 3.0
    return 2.0

def score_divergence(ret_a, ret_b):       # 双雄区间收益分化
    if ret_a is None or ret_b is None: return None
    d = abs(ret_a - ret_b)
    if d >= 0.15: return 4.5
    if d >= 0.10: return 4.0
    if d >= 0.06: return 3.5
    if d >= 0.03: return 3.0
    return 2.5

def score_vkospi(v):                       # 来自 state(手填的一个数)
    if v is None: return None
    if v >= 60: return 5.0
    if v >= 45: return 4.5
    if v >= 40: return 4.0
    if v >= 30: return 3.0
    if v >= 25: return 2.0
    return 1.0

def score_foreign(rows):                   # Naver 投资者动向: 外资净卖强度/连续性 -> (分, flow_status)
    if not rows: return None, None
    fl = [r["foreign"] for r in rows]      # 억원, 最新在前, 负=净卖
    latest, cum = fl[0], sum(fl)
    streak = 0
    for v in fl:
        if v < 0: streak += 1
        else: break
    if latest >= 0:
        return (2.0, "净买/回补" if latest > 0 else "中性")
    if streak >= 10 or cum <= -60000: return (5.0, "连续净卖/创纪录")
    if streak >= 5:  return (4.5, "连续净卖/创纪录")
    if streak >= 3:  return (4.0, "温和净卖")
    return (3.5, "温和净卖")

def score_domestic(rows):                  # 个人(散户)接盘 -> (分, domestic_status)
    if not rows: return None, None
    indiv, foreign = rows[0]["indiv"], rows[0]["foreign"]
    if indiv <= 0:                         # 散户转卖 = 买盘枯竭
        return (4.5, "接不动/枯竭")
    if foreign < -20000:                   # 外资大举抛(>2万亿)时看散户吸收比=indiv/|foreign|
        ratio = indiv / abs(foreign)
        if ratio < 0.85: return (4.0, "边际减弱")   # 还在买但接不平外资抛压
        if ratio < 1.10: return (3.5, "正常")       # 刚好接住
        return (3.2, "正常")                          # 轻松接住(仍是脆弱杠杆接盘)
    return (3.0, "正常")

def score_us_ai(avg5d):                    # 美股AI龙头(NVDA/MU/AVGO)5日均涨幅: 越弱越危险
    if avg5d is None: return None
    if avg5d > 5:  return 2.0
    if avg5d > 0:  return 3.0
    if avg5d > -5: return 4.0
    return 5.0

def score_upshadow(upsh, volr):            # 放量上影: 上影占比 × 量比
    if upsh is None: return None
    s = 4.0 if upsh >= 0.5 else 3.0 if upsh >= 0.3 else 2.0
    if volr and volr >= 1.3: s = min(5.0, s + 0.5)
    return s

# ---------- 27 指标定义 ----------
# kind: 'auto' 用 auto_fn 算; 'manual' 从 state['manual'][key] 读
INDICATORS = [
 # 杠杆/强平 (24)
 dict(key="L1", cat="杠杆/强平", w=6, name="全市场融资余额 level+change", dir="正向", kind="manual", note="38.02tn 创纪录(5/29)", src="S2"),
 dict(key="L2", cat="杠杆/强平", w=6, name="Samsung+Hynix 融资集中度", dir="正向", kind="manual", note="两大股融资 7.79tn(+208%YTD)", src="S2"),
 dict(key="L3", cat="杠杆/强平", w=5, name="强平/margin call 趋势", dir="正向", kind="manual", note="5/21 飙917亿", src="S3"),
 dict(key="L4", cat="杠杆/强平", w=3, name="신용융자 이용률/券商额度收紧", dir="正向", kind="manual", note="额度逼近上限", src="S3"),
 dict(key="L5", cat="杠杆/强平", w=2, name="单股/指数杠杆ETF净流入", dir="正向", kind="manual", note="2x单股杠杆吸金", src="S6"),
 dict(key="L6", cat="杠杆/强平", w=2, name="3月暴跌后再杠杆速度", dir="正向", kind="manual", note="32tn→38tn<90日", src="S8"),
 # 资金结构 (22)
 dict(key="F1", cat="资金结构", w=6, name="外资净卖强度/连续性", dir="正向", kind="auto", auto="foreign", note="Naver投资者动向(억원)", src="S4"),
 dict(key="F2", cat="资金结构", w=4, name="外资卖盘集中 Samsung/Hynix", dir="正向", kind="manual", note="高度集中双雄", src="S4"),
 dict(key="F3", cat="资金结构", w=4, name="国内接盘质量(散户/融资)", dir="正向", kind="auto", auto="domestic", note="散户净买吸收外资抛压", src="S4"),
 dict(key="F4", cat="资金结构", w=3, name="韩元与股票共振走弱(USD/KRW)", dir="正向", kind="auto", auto="usdkrw", note="1,516(17年低)", src="S9"),
 dict(key="F5", cat="资金结构", w=2, name="外资借券做空/空头禁令状态", dir="正向", kind="manual", note="2025/3/31解禁", src="S12"),
 dict(key="F6", cat="资金结构", w=3, name="NPS/养老金政策托底(反身衰减)", dir="逆向", kind="manual", note="24.5%>目标20.8%超配", src="S13"),
 # 市场结构 (15)
 dict(key="M1", cat="市场结构", w=5, name="Samsung+Hynix 占 KOSPI 市值", dir="正向", kind="auto", auto="concentration", note="≈KOSPI半数", src="S5"),
 dict(key="M2", cat="市场结构", w=3, name="科技/半导体盈利贡献集中度", dir="正向", kind="manual", note="盈利高度集中", src="S6"),
 dict(key="M3", cat="市场结构", w=5, name="广度恶化(跌>涨/加权背离)", dir="正向", kind="auto", auto="breadth", note="创新高日10只跌9", src="S6"),
 dict(key="M4", cat="市场结构", w=2, name="被动盘/程序化交易拥挤", dir="正向", kind="manual", note="被动盘接外资", src="S6"),
 # 价格行为 (19)
 dict(key="P1", cat="价格行为", w=4, name="Hynix 进入 245w-250w 清仓区", dir="正向", kind="auto", auto="hynix_zone", note="清仓价 245w-250w", src="S5"),
 dict(key="P2", cat="价格行为", w=5, name="好消息不涨/冲高回落", dir="正向", kind="manual", note="利好不涨苗头", src="S1"),
 dict(key="P3", cat="价格行为", w=3, name="Samsung 与 Hynix 分化", dir="正向", kind="auto", auto="divergence", note="涨幅分化", src="S5"),
 dict(key="P4", cat="价格行为", w=5, name="K200 破位并反抽失败【右侧总闸】", dir="正向", kind="manual", note="尚未破位", src="S1"),
 dict(key="P5", cat="价格行为", w=2, name="放量上影/买盘效率下降", dir="正向", kind="auto", auto="upshadow", note="海力士上影占比×量比", src="S7"),
 # 波动/宏观/AI外溢 (20)
 dict(key="V1", cat="波动/宏观/AI外溢", w=4, name="VKOSPI 高位/不回落", dir="正向", kind="auto", auto="vkospi", note="~70 极端高", src="S7"),
 dict(key="V2", cat="波动/宏观/AI外溢", w=3, name="put/call skew 与 K200 对冲成本", dir="正向", kind="manual", note="put贵负carry", src="S7"),
 dict(key="V3", cat="波动/宏观/AI外溢", w=4, name="ELS/ELB knock-in 反身性", dir="正向", kind="manual", note="发行6.8x;距in 40-60%", src="S_ELS"),
 dict(key="V4", cat="波动/宏观/AI外溢", w=3, name="DRAM/DDR5 现货价拐点(vs HBM背离)", dir="正向", kind="manual", note="DDR5见顶HBM仍升", src="S_DRAM"),
 dict(key="V5", cat="波动/宏观/AI外溢", w=3, name="美股AI龙头+Micron/NVDA外溢", dir="正向", kind="auto", auto="us_ai", note="NVDA/MU/AVGO 5日动能", src="S_DRAM"),
 dict(key="V6", cat="波动/宏观/AI外溢", w=3, name="AI capex/ROI 叙事反转", dir="正向", kind="manual", note="叙事未反转", src="S_DRAM"),
]

CATS = ["杠杆/强平", "资金结构", "市场结构", "价格行为", "波动/宏观/AI外溢"]

# ---------- 动作带 / gating / IV 阈值 ----------
def band(total):
    if total < 35: return ("只记录", "0–35 低风险 / 只记录", "#63BE7B")
    if total < 55: return ("监控", "35–55 中风险 / 重点监控", "#A9D08E")
    if total < 70: return ("观察仓", "55–70 高风险 / 观察仓区", "#FFC000")
    if total < 82: return ("第一笔", "70–82 第一笔 put 条件接近", "#ED7D31")
    return ("主仓区", "82–100 去杠杆确认 / 主仓区", "#C00000")

def iv_gate(vkospi):
    if vkospi is None: return ("⚪ 未知", "请在 state.json 填 VKOSPI", 1.0, "#9E9E9E")
    if vkospi >= 60: return ("🔴 过贵", "禁裸 put(负EV)。只用 put 价差/缩仓×0.4，或等回落<40", 0.4, "#7030A0")
    if vkospi >= 45: return ("🔴 过贵", "优先 put 价差，仓位×0.6", 0.6, "#C00000")
    if vkospi >= 28: return ("🟡 偏贵", "裸 put 可小仓，或近月价差(×0.8)", 0.8, "#FFC000")
    return ("🟢 可裸买", "IV 正常，裸 put 可接受", 1.0, "#63BE7B")

GATE_PRICE_CAT_MIN = 60     # 价格行为类别分门槛
GATE_TOTAL_MIN = 70
GATE_ADD_TOTAL_MIN = 82
FRESH_DAYS = 5              # 手动核心指标新鲜度阈值(自然日)
