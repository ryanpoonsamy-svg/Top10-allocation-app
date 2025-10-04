import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
import io

# ---------- PAGE SETUP ----------
st.set_page_config(page_title="Top 10 Allocation", layout="centered")

# ---------- CONSTANTS ----------
TICKERS = ["AAPL","MSFT","NVDA","GOOGL","GOOG","AMZN","META","AVGO","TSLA","BRK-B"]

# ---------- FUNCTIONS ----------
def fetch_data(tickers):
    rows = []
    for tk in tickers:
        tkr = yf.Ticker(tk)
        price = tkr.fast_info.get("last_price")
        shares = tkr.fast_info.get("shares")
        mcap = tkr.fast_info.get("market_cap")
        if not price or not mcap:
            continue
        rows.append({"Ticker": tk, "Price": price, "Market Cap": mcap})
    df = pd.DataFrame(rows)
    df["Market Cap ($T)"] = df["Market Cap"] / 1e12
    return df.sort_values("Market Cap", ascending=False)

def get_fx():
    fx = yf.Ticker("GBPUSD=X")
    return fx.fast_info.get("last_price") or 1.25

# ---------- UI ----------
st.title("Top 10 Stock Allocation")

# Input allocation (defaults to £37,000)
gbp_budget = st.number_input("💷 Allocation amount (£)", min_value=0, value=37000, step=1000)

# Refresh button
if st.button("🔄 Refresh Data"):
    df = fetch_data(TICKERS)
    fx = get_fx()
    fx_used = fx if fx else 1.25
    fx_usd_to_gbp = 1 / fx_used
    usd_budget = gbp_budget * fx_used

    df["Weight %"] = df["Market Cap"] / df["Market Cap"].sum()
    df["$ Allocation"] = usd_budget * df["Weight %"]
    df["£ Allocation"] = df["$ Allocation"] * fx_usd_to_gbp
    df["Est. Shares"] = df["$ Allocation"] / df["Price"]

    df = df[["Ticker","Price","Market Cap","Market Cap ($T)","Weight %","$ Allocation","£ Allocation","Est. Shares"]]
    df_display = df.copy()
    df_display["Price"] = df_display["Price"].map(lambda x: f"${x:,.2f}")
    df_display["Market Cap"] = df_display["Market Cap"].map(lambda x: f"${x:,.0f}")
    df_display["Market Cap ($T)"] = df_display["Market Cap ($T)"].map(lambda x: f"{x:,.2f}")
    df_display["Weight %"] = df_display["Weight %"].map(lambda x: f"{x:.2%}")
    df_display["$ Allocation"] = df_display["$ Allocation"].map(lambda x: f"${x:,.0f}")
    df_display["£ Allocation"] = df_display["£ Allocation"].map(lambda x: f"£{x:,.0f}")
    df_display["Est. Shares"] = df_display["Est. Shares"].map(lambda x: f"{x:,.1f}")

    st.dataframe(df_display, use_container_width=True)
    timestamp = datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m-%d %H:%M:%S")
    st.caption(f"Data source: Yahoo Finance | Updated {timestamp} | GBP→USD: {fx_used:.4f}")

    # Excel download
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Allocation")
    st.download_button("⬇️ Download Excel", data=output.getvalue(), file_name="top10_watchlist.xlsx")
else:
    st.info("Press 🔄 Refresh Data to load the latest figures.")
