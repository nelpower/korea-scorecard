# -*- coding: utf-8 -*-
"""找一个能免费、可靠拿到 VKOSPI(코스피200 변동성지수) 的数据源。今天应≈70。"""
import warnings; warnings.filterwarnings("ignore")
import requests, json
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def show(name, fn):
    try:
        print(f"--- {name} ---"); print(fn())
    except Exception as e:
        print(f"--- {name} --- FAIL {type(e).__name__}: {str(e)[:120]}")

# 1) stooq CSV (无需鉴权, 对爬虫友好)
for s in ["^vkospi", "vkospi", "^vks", "vkospi.kr"]:
    show(f"stooq last {s}", lambda s=s: requests.get(
        f"https://stooq.com/q/l/?s={s}&f=sd2t2ohlc&e=csv", headers=H, timeout=15).text.strip())

# 2) stooq daily history
show("stooq hist ^vkospi", lambda: requests.get(
    "https://stooq.com/q/d/l/?s=^vkospi&i=d", headers=H, timeout=15).text.strip()[-200:])

# 3) Naver mobile index API
for code in ["VKOSPI", "KSVKOSPI", ".VKOSPI"]:
    show(f"naver m.stock {code}", lambda code=code: requests.get(
        f"https://m.stock.naver.com/api/index/{code}/basic", headers=H, timeout=15).text[:200])

# 4) Naver siseJson alt symbols
for sym in ["VKOSPI", "KPI200VKOSPI", "변동성지수"]:
    show(f"naver siseJson {sym}", lambda sym=sym: requests.get(
        "https://api.finance.naver.com/siseJson.naver",
        params={"symbol": sym, "requestType": 1, "startTime": "20260529",
                "endTime": "20260602", "timeframe": "day"}, headers=H, timeout=15).text.strip())

# 5) yfinance alt tickers
try:
    import yfinance as yf
    for tk in ["^VKOSPI", "VKOSPI", "VKOSPI.KS", "^KVKOSPI"]:
        try:
            h = yf.Ticker(tk).history(period="5d")
            print(f"--- yf {tk} --- rows={len(h)}", (float(h['Close'].iloc[-1]) if len(h) else "empty"))
        except Exception as e:
            print(f"--- yf {tk} --- FAIL {str(e)[:60]}")
except Exception as e:
    print("yfinance load fail", e)

# 6) FDR stooq source
try:
    import FinanceDataReader as fdr
    for s in ["^VKOSPI", "VKOSPI"]:
        try:
            df = fdr.DataReader(s, "2026-05-25", data_source="stooq")
            print(f"--- fdr stooq {s} --- rows={len(df)}", (float(df['Close'].iloc[-1]) if len(df) else "empty"))
        except Exception as e:
            print(f"--- fdr stooq {s} --- FAIL {str(e)[:60]}")
except Exception as e:
    print("fdr load fail", e)
