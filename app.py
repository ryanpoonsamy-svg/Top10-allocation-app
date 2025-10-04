# app.py — Top 10 Allocation (Finnhub) with explicit FX used

import io
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import pandas as pd
import numpy as np

# ---------------- Page setup (wide) ----------------
st.set_page_config(page_title="Top 10 Allocation", layout="wide")
st.markdown("""
<style>
  .block-container { max-width: 1400px; padding-top: 0.5rem; padding-bottom: 0.5rem; }
</style>
""", unsafe_allow_html=True)

# ---------------- Settings ----------------
TICKERS = ["AAPL","MSFT","NVDA","GOOGL","GOOG","AMZN","META","AVGO","TSLA","BRK-B"]
DEFAULT_GBP_BUDGET = 37_000

API_KEY = st.secrets.get("FINNHUB_KEY") or os.environ.get("FINNHUB_KEY")
BASE = "https://finnhub.io/api/v1"

if not API_KEY:
    st.error("Missing Finnhub API key. In Streamlit Cloud, go to ••• Manage app → Settings → Secrets and add:\n\nFINNHUB_KEY = \"YOUR_KEY_HERE\"")
    st.stop()

# ---------------- HTTP helper with retry ----------------
def _get(url, params=None, retries=3, sleep=0.6):
    if params is None: params = {}
    params["token"] = API_KEY
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(sleep * (2 ** i))
    return None

# ---------------- Live FX (returns BOTH directions used) ----------------
@st.cache_data(ttl=30)
def get_fx_rates():
    """
    Returns: (gbp_to_usd, usd_to_gbp, source_note)
      gbp_to_usd = USD per 1 GBP  (used to convert your £ budget to $)
      usd_to_gbp = GBP per 1 USD  (used to convert $ allocations back to £)
    """
    # Primary: aggregated rates with GBP as base
    data = _get(f"{BASE}/forex/rates", params={"base": "GBP"})
    if data and isinstance(data.get("quote"), dict) and data["quote"].get("USD"):
        gbp_to_usd = float(data["quote"]["USD"])
        return gbp_to_usd, 1.0 / gbp_to_usd, "Finnhub forex/rates"

    # Backup: last OANDA candle close for GBP/USD (5m window)
    now = int(time.time())
    candles = _get(
        f"{BASE}/forex/candle",
        params={"symbol": "OANDA:GBP_USD", "resolution": "5", "from": now - 3600, "to": now},
    )
    if candles and candles.get("s") == "ok" and candles.get("c"):
        gbp_to_usd = float(candles["c"][-1])
        return gbp_to_usd, 1.0 / gbp_to_usd, "Finnhub OANDA candle"

    # Final fallback (only if API unavailable)
    gbp_to_usd = 1.25
    return gbp_to_usd, 1.0 / gbp_to_usd, "fallback 1.25"

# ---------------- Market data ----------------
@st.cache_data(ttl=60)
def fetch_data(tickers):
    rows = []
    for tk in tickers:
        q = _get(f"{BASE}/quote", params={"symbol": tk})
        p = _get(f"{BASE}/stock/profile2", params={"symbol": tk})
        try:
            price = float(q.get("c", 0)) if q else 0.0
            mcap_bil = float(p.get("marketCapitalization", 0)) if p else 0.0  # USD billions
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
        # 👉 Show the EXACT numbers used in the math (both directions)
        st.caption(
            f"Data source: Finnhub.io | Updated {ts} | "
            f"GBP→USD used: {gbp_to_usd:.6f} | USD→GBP used: {usd_to_gbp:.6f} ({fx_src})"
        )

        # Excel download
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
