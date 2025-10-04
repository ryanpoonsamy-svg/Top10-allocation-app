import io
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import pandas as pd
import numpy as np

# ---------- Page config ----------
st.set_page_config(page_title="Top 10 Allocation", layout="centered")

# ---------- Settings ----------
TICKERS = ["AAPL","MSFT","NVDA","GOOGL","GOOG","AMZN","META","AVGO","TSLA","BRK-B"]
API_KEY = st.secrets.get("FINNHUB_KEY", "PASTE_KEY_HERE")  # replace if running locally without secrets

BASE = "https://finnhub.io/api/v1"

def _get(url, params=None, retries=3, sleep=0.5):
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

@st.cache_data(ttl=60)
def get_fx_gbp_usd():
    """GBP→USD; Finnhub returns a dict of rates when base=GBP."""
    data = _get(f"{BASE}/forex/rates", params={"base": "GBP"})
    if not data or "quote" not in data or "USD" not in data["quote"]:
        return 1.25  # fallback
    return float(data["quote"]["USD"])

@st.cache_data(ttl=60)
def fetch_data(tickers):
    """Use Finnhub for live price (quote) and market cap (profile2)."""
    rows = []
    for tk in tickers:
        # Quote endpoint: current price 'c'
        q = _get(f"{BASE}/quote", params={"symbol": tk})
        # Profile2 endpoint: marketCapitalization in USD (billions)
        p = _get(f"{BASE}/stock/profile2", params={"symbol": tk})
        try:
            price = float(q.get("c", 0)) if q else 0.0
            mcap_bil = float(p.get("marketCapitalization", 0)) if p else 0.0  # USD billions
            if price > 0 and mcap_bil > 0:
                rows.append({
                    "Ticker": tk,
                    "Price": price,
                    "Market Cap": mcap_bil * 1e9  # convert billions → dollars
                })
        except Exception:
            continue
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["Market Cap ($T)"] = df["Market Cap"] / 1e12
    df = df.sort_values("Market Cap", ascending=False).reset_index(drop=True)
    return df

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

# ---------- UI ----------
st.title("Top 10 Stock Allocation")

gbp_budget = st.number_input("💷 Allocation amount (£)", min_value=0, value=37000, step=1000)

if st.button("🔄 Refresh Data"):
    with st.spinner("Fetching live data…"):
        df = fetch_data(TICKERS)
        fx_gbpusd = get_fx_gbp_usd()  # USD per 1 GBP
        usd_to_gbp = 1.0 / fx_gbpusd
        usd_budget = gbp_budget * fx_gbpusd

    if df.empty:
        st.warning("No data retrieved from Finnhub. Please try again in a moment.")
        # Still show a blank table header for consistency
        st.dataframe(pd.DataFrame(columns=["Ticker","Price","Market Cap","Market Cap ($T)","Weight %","$ Allocation","£ Allocation","Est. Shares"]),
                     use_container_width=True)
    else:
        total_mcap = df["Market Cap"].sum()
        df["Weight %"] = df["Market Cap"] / total_mcap
        df["$ Allocation"] = usd_budget * df["Weight %"]
        df["£ Allocation"] = df["$ Allocation"] * usd_to_gbp
        df["Est. Shares"] = df["$ Allocation"] / df["Price"]
        out_df = df[["Ticker","Price","Market Cap","Market Cap ($T)","Weight %","$ Allocation","£ Allocation","Est. Shares"]]

        # Show nicely formatted table
        st.dataframe(format_for_display(out_df), use_container_width=True)

        # Source/timestamp
        ts = datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m-%d %H:%M:%S")
        st.caption(f"Data source: Finnhub.io | Updated {ts} | GBP→USD: {fx_gbpusd:.6f}")

        # Excel download
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
            out_df.to_excel(writer, index=False, sheet_name="Allocation")
        st.download_button("⬇️ Download Excel",
                           data=buf.getvalue(),
                           file_name="top10_watchlist.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
else:
    st.info("Press 🔄 Refresh Data to load the latest figures.")
