# app.py — Top 10 Allocation (effective FX from table totals + robust FX sources + diagnostics)

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

# ---------------- Helpers ----------------
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

@st.cache_data(ttl=60)
def get_fx_gbp_usd_with_sources():
    """
    Return (gbp_to_usd, usd_to_gbp, source_note, details_dict)
    Tries:
      1) ECB via exchangerate.host (GBP base)
      2) ECB via exchangerate.host (USD base, inverted)
      3) Finnhub /forex/rates (base=GBP)
      4) Finnhub OANDA candles (GBP/USD) last close
      5) Fallback 1.35
    """
    details = {}

    # 1) ECB (GBP base)
    try:
        r = requests.get("https://api.exchangerate.host/latest",
                         params={"base": "GBP", "symbols": "USD"}, timeout=10)
        j = r.json()
        details["ecb_gbp_base_raw"] = j
        v = j.get("rates", {}).get("USD")
        if v and float(v) > 0:
            gbp_to_usd = float(v)
            return gbp_to_usd, 1.0 / gbp_to_usd, "ECB (GBP base)", details
    except Exception as e:
        details["ecb_gbp_base_error"] = str(e)

    # 2) ECB (USD base) → invert
    try:
        r = requests.get("https://api.exchangerate.host/latest",
                         params={"base": "USD", "symbols": "GBP"}, timeout=10)
        j = r.json()
        details["ecb_usd_base_raw"] = j
        v = j.get("rates", {}).get("GBP")
        if v and float(v) > 0:
            usd_to_gbp = float(v)
            gbp_to_usd = 1.0 / usd_to_gbp
            return gbp_to_usd, usd_to_gbp, "ECB (USD base, inverted)", details
    except Exception as e:
        details["ecb_usd_base_error"] = str(e)

    # 3) Finnhub aggregated
    data = _get_finnhub("forex/rates", params={"base": "GBP"})
    details["finnhub_rates_raw"] = data
    if data and isinstance(data.get("quote"), dict):
        v = data["quote"].get("USD")
        if v:
            try:
                gbp_to_usd = float(v)
                return gbp_to_usd, 1.0 / gbp_to_usd, "Finnhub forex/rates", details
            except Exception as e:
                details["finnhub_rates_parse_error"] = str(e)

    # 4) Finnhub OANDA candles
    now = int(time.time())
    for res in ("1", "5", "15"):
        candles = _get_finnhub("forex/candle",
                               params={"symbol": "OANDA:GBP_USD",
                                       "resolution": res,
                                       "from": now - 6*3600,
                                       "to": now})
        details[f"finnhub_candle_{res}m_raw"] = candles
        if candles and candles.get("s") == "ok" and candles.get("c"):
            try:
                gbp_to_usd = float(candles["c"][-1])
                if gbp_to_usd > 0:
                    return gbp_to_usd, 1.0 / gbp_to_usd, f"Finnhub OANDA candle {res}m", details
            except Exception as e:
                details[f"finnhub_candle_{res}m_parse_error"] = str(e)

    # 5) Fallback
    gbp_to_usd = 1.35
    details["fallback_used"] = True
    return gbp_to_usd, 1.0 / gbp_to_usd, "fallback 1.35", details

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
    # Clear caches each refresh so we don't reuse a fallback unintentionally
    st.cache_data.clear()

    with st.spinner("Fetching live data…"):
        df = fetch_data(TICKERS)
        gbp_to_usd, usd_to_gbp, fx_src, fx_details = get_fx_gbp_usd_with_sources()
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

        # Full-width table
        row_height = 36
        st.dataframe(
            format_for_display(out_df_display),
            use_container_width=True,
            height=(len(out_df_display) + 2) * row_height
        )

        # --- Compute EFFECTIVE FX from totals (exactly matches table math) ---
        total_usd = float(out_df["$ Allocation"].sum())
        total_gbp = float(out_df["£ Allocation"].sum())
        # Protect against division by zero
        effective_rate = (total_usd / total_gbp) if total_gbp else float("nan")
        inverse_rate = (1.0 / effective_rate) if (effective_rate and effective_rate > 0) else float("nan")

        ts = datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m-%d %H:%M:%S")
        st.caption(
            f"Data source: Finnhub.io | Updated {ts} | "
            f"Effective GBP→USD used: {effective_rate:.6f} | USD→GBP used: {inverse_rate:.6f} "
            f"(FX source tried: {fx_src})"
        )

        # Download Excel (with effective rate + timestamp written under the table)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
            out_df.to_excel(writer, index=False, sheet_name="Allocation")
            ws = writer.sheets["Allocation"]
            nrows = len(out_df) + 3
            ws.write(nrows, 0, f"Effective GBP→USD rate used: {effective_rate:.6f}")
            ws.write(nrows + 1, 0, f"USD→GBP rate used: {inverse_rate:.6f}")
            ws.write(nrows + 2, 0, f"Updated: {ts}")
        st.download_button(
            "⬇️ Download Excel",
            data=buf.getvalue(),
            file_name="top10_watchlist.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        with st.expander("Diagnostics (FX sources & raw responses)"):
            st.write("Selected FX source:", fx_src)
            st.write("GBP→USD fetched (pre-effective):", gbp_to_usd)
            st.write("USD→GBP fetched (pre-effective):", usd_to_gbp)
            st.json(fx_details)

else:
    st.info("Press 🔄 Refresh Data to load the latest figures.")
