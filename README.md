# 🇰🇷 韩国去杠杆做空择时评分卡（自动刷新版）

KOSPI 200 Put 建仓时机表。每个韩股交易日收盘后自动抓数、重算 0–100 分、生成手机网页。
**右侧交易、不抄顶**：等「外资卖 + 国内接不动 + 杠杆反噬 + K200 破位反抽失败」四共振才动手。

> ⚠️ 个人择时辅助工具，非投资建议、非预测。期权可能归零，务必控制仓位与到期。

## 怎么跑的
- **GitHub Actions** 定时 `cron: 42 6 * * 1-5`（= **15:42 KST**，韩股收盘 15:30 后错峰；UTC 06:42）。
  GitHub cron 偶尔延迟几分钟，数据此时已结算，无碍。韩国假日/周末无新数据则跳过提交。
- `refresh.py` 抓数 → 算分 → 追加 `data_history.csv` → 生成 `index.html` + `status.json`，再提交回仓库。
- 也可在 Actions 页面手动 **Run workflow**，或改 `state.json`/`config.py`/`refresh.py` 推送后立即重算。

## 自动 vs 手填（10 项自动 / 17 项手填）
**自动抓取**（每次运行实时，失败则沿用上次值）：
- KOSPI / KOSPI200 / 三星 / 海力士 / USD-KRW 收盘价、市值集中度、市场广度、双雄分化、Hynix 清仓区　← FinanceDataReader
- **VKOSPI**　← ScraperAPI 代理抓 investing.com（GitHub secret `SCRAPERAPI_KEY`；失败沿用 state.json）
- **外资净流 + 散户接盘**（驱动触发器①②）　← Naver 投资者动向；同时自动判定 `flow_status`/`domestic_status`
- **美股 AI 外溢**（NVDA/MU/AVGO 5 日动能）　← yfinance
- **放量上影**（海力士上影占比×量比）　← OHLCV

**手填**（纯判断，或无免费数据源——编辑 `state.json`）：
- `k200_status` K200 技术状态（**右侧总闸，故意保留人工**）
- `manual{}`：融资余额/强平/신용융자/外资卖盘集中/半导体盈利/被动盘/好消息不涨/ELS/skew/DRAM/AI capex 等打分（0–5）
- `vkospi` 仅作 ScraperAPI 失败时的兜底值；`hynix_lower/upper` 清仓区、`risk_budget_pct`、`manual_update` 复核日期
- `flow_status`/`domestic_status` 仅作 Naver 抓取失败时的兜底

> ⚠️ `k200_status` 只有选到 **「跌破后反抽失败」/「周线破位反抽失败」** 才点亮右侧总闸（触发器④）。仅「跌破支撑待反抽」不放行——这是不抄顶的硬约束。

## 在手机上更新手填项
GitHub App 或手机浏览器打开仓库 → `state.json` → 铅笔图标编辑 → Commit。
几分钟后 Action 自动重算，刷新网页即见新分。`manual_update` 改成当天日期可让置信度回到 100%。

## 本地运行
```bash
pip install -r requirements.txt
python refresh.py        # 生成 index.html / status.json / data_history.csv
python -m http.server 8012   # 浏览器开 http://localhost:8012
```

## 数据来源与诚实边界
- 价格/指数/汇率/市值/广度：FinanceDataReader（Naver/KRX 公开数据）。
- VKOSPI：经 ScraperAPI 住宅代理抓 investing.com（绕过 GitHub 机房 IP 的 Cloudflare 403）。免费额度 1000/月，每天 1 次足够；key 存 GitHub secret，不入公开代码。
- 外资净流/散户接盘：Naver 投资者动向（投资者별 순매매, 단위 억원）。**注意 KRX 收盘后约 1h 才定稿，故刷新定在 17:37 KST。**
- 美股 AI 外溢：yfinance（NVDA/MU/AVGO）。放量上影：海力士日 OHLCV 计算。
- **仍无法自动**：融资余额/강제반대매매（pykrx 因 KRX 改版需登录、KOFIA 为 SPA）、ELS knock-in、put/call skew、NPS 配比、DRAM 现货、AI capex 叙事——无免费结构化源或需终端，手填。
- **K200 反抽失败 / 好消息不涨**：技术可机械化，但**故意保留人工**——它们是右侧总闸，机械化会重蹈「提前抄顶」。
- 评分方法与 Excel 版（`korea_deleveraging_short_timing_scorecard_v2.xlsx`）一致：27 指标 5 类、方向感知、右侧 gating、VKOSPI IV 闸、数据置信度。
