# app.py — Top 10 Allocation (accurate FX + 1.35 fallback)

import io
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import pandas as pd
import numpy as np

# ---------------- Page setup ----------------
st.set_page_config(page_title="Top 10 Allocation", layout="wide")
st.markdown("""
<style>
  .block-container { max-width: 1400px; padding-top: 1.5rem; padding-bottom: 0.75rem; }
  h1, .h1 { margin-top: .25rem; }
</style>
""", unsafe_allow_html=True)

# ---------------- Settings ----------------
TICKERS = ["AAPL","MSFT","NVDA","GOOGL","GOOG","AMZN","META","AVGO","TSLA","BRK-B"]
DEFAULT_GBP_BUDGET = 37_000

API_KEY = st.secrets.get("FINNHUB_KEY") or os.environ.get("FINNHUB_KEY")
FINNHUB_BASE = "https://finnhub.io/api/v1"

if not API_KEY:
    st.error("Missing Finnhub API key. In Streamlit Cloud: ••• Manage app → Settings → Secrets → add\n\nFINNHUB_KEY = \"YOUR_KEY_HERE\"")
    st.stop()

# ---------------- HTTP helpers ----------------
def _get_finnhub(path, params=None, retries=3, sleep=0.6):
    if params is None: params = {}
    params["token"] = API_KEY
    url = f"{FINNHUB_BASE}/{path.lstrip('/')}"
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(sleep * (2 ** i))
    return None

# ---------------- FX (GBP→USD) — ECB first, then Finnhub, then fallback 1.35 ----------------
@st.cache_data(ttl=60)
def get_fx_rates():
    """
    Returns: (gbp_to_usd, usd_to_gbp, source_note)
      1) ECB via exchangerate.host
      2) Finnhub /forex/rates (base=GBP)
      3) Finnhub OANDA candles
      4) Fallback 1.35 (clearly labeled)
    """
    # 1) ECB (exchangerate.host)
    try:
        r = requests.get("https://api.exchangerate.host/latest",
                         params={"base": "GBP", "symbols": "USD"},
                         timeout=10)
        j = r.json()
        v = j.get("rates", {}).get("USD")
        if v and float(v) > 0:
            gbp_to_usd = float(v)
            return gbp_to_usd, 1.0 / gbp_to_usd, "exchangerate.host (ECB)"
    except Exception:
        pass

    # 2) Finnhub aggregated rates
    data = _get_finnhub("forex/rates", params={"base": "GBP"})
    if data and isinstance(data.get("quote"), dict):
        v = data["quote"].get("USD")
        if v:
            try:
                gbp_to_usd = float(v)
                return gbp_to_usd, 1.0 / gbp_to_usd, "Finnhub forex/rates"
            except Exception:
                pass

    # 3) Finnhub OANDA candles
    now = int(time.time())
    for res in ("1", "5", "15"):
        candles = _get_finnhub("forex/candle",
                               params={"symbol": "OANDA:GBP_USD",
                                       "resolution": res,
                                       "from": now - 6*3600,
                                       "to": now})
        if candles and candles.get("s") == "ok" and candles.get("c"):
            try:
                gbp_to_usd = float(candles["c"][-1])
                if gbp_to_usd > 0:
                    return gbp_to_usd, 1.0 / gbp_to_usd, f"Finnhub OANDA candle {res}m"
            except Exception:
                pass

    # 4) Fallback — use 1.35 instead of 1.25
    gbp_to_usd = 1.35
    return gbp_to_usd, 1.0 / gbp_to_usd, "fallback 1.35"

# ---------------- Market data ----------------
@st.cache_data(ttl=60)
def fetch_data(tickers):
    rows = []
    for tk in tickers:
        q = _get_finnhub("quote", params={"symbol": tk})
        p = _get_finnhub("stock/profile2", params={"symbol": tk})
        try:
            price = float(q.get("c", 0)) if q else 0.0
            mcap_bil = float(p.get("marketCapitalization", 0)) if p else 0.0
            if price > 0 and mcap_bil > 0:
                rows.append({"Ticker": tk, "Price": price, "Market Cap": mcap_bil * 1e9})
        except Exception:
            continue
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["Market Cap ($T)"] = df["Market Cap"] / 1e12
    return df.sort_values("Market Cap", ascending=False).reset_index(drop=True)

# ---------------- Formatting ----------------
def format_for_display(df):
    d = df.copy()
    d["Price"] = d["Price"].map(lambda x: f"${x:,.2f}")
    d["Market Cap"] = d["Market Cap"].map(lambda x: f"${x:,.0f}")
    d["Market Cap ($T)"] = d["Market Cap ($T)"].map(lambda x: f"{x:,.2f}")
    d["Weight %"] = d["Weight %"].map(lambda x: f"{x:.2%}")
    d["$ Allocation"] = d["$ Allocation"].map(lambda x: f"${x:,.0f}")
    d["£ Allocation"] = d["£ Allocation"].map(lambda x: f"£{x:,.0f}")
    d["Est. Shares"] = d["Est. Shares"].map(lambda x: f"{x:,.1f}")
    return d

# ---------------- UI ----------------
st.title("Top 10 Stock Allocation")

gbp_budget = st.number_input("💷 Allocation amount (£)", min_value=0, value=DEFAULT_GBP_BUDGET, step=1000)

if st.button("🔄 Refresh Data"):
    # Clear cache to always pull fresh FX
    st.cache_data.clear()

    with st.spinner("Fetching live data…"):
        df = fetch_data(TICKERS)
        gbp_to_usd, usd_to_gbp, fx_src = get_fx_rates()
        usd_budget = gbp_budget * gbp_to_usd

    if df.empty:
        st.warning("No data retrieved from Finnhub. Please try again shortly.")
        st.dataframe(
            pd.DataFrame(columns=["Ticker","Price","Market Cap","Market Cap ($T)","Weight %","$ Allocation","£ Allocation","Est. Shares"]),
            use_container_width=True,
            height=240
        )
    else:
        total_mcap = df["Market Cap"].sum()
        df["Weight %"] = df["Market Cap"] / total_mcap
        df["$ Allocation"] = usd_budget * df["Weight %"]
        df["£ Allocation"] = df["$ Allocation"] * usd_to_gbp
        df["Est. Shares"] = df["$ Allocation"] / df["Price"]

        out_df = df[["Ticker","Price","Market Cap","Market Cap ($T)","Weight %","$ Allocation","£ Allocation","Est. Shares"]]
        out_df_display = out_df.copy()
        out_df_display.index = out_df_display.index + 1
        out_df_display.index.name = "Rank"

        row_height = 36
        st.dataframe(
            format_for_display(out_df_display),
            use_container_width=True,
            height=(len(out_df_display) + 2) * row_height
        )

        ts = datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m-%d %H:%M:%S")
        st.caption(
            f"Data source: Finnhub.io | Updated {ts} | "
            f"GBP→USD used: {gbp_to_usd:.6f} | USD→GBP used: {usd_to_gbp:.6f} ({fx_src})"
        )

        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
            out_df.to_excel(writer, index=False, sheet_name="Allocation")
        st.download_button(
            "⬇️ Download Excel",
            data=buf.getvalue(),
            file_name="top10_watchlist.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
else:
    st.info("Press 🔄 Refresh Data to load the latest figures.")
