import streamlit as st
import pandas as pd
import numpy as np
import requests
import time
import plotly.graph_objects as go

# =========================
# 設定
# =========================
BINANCE_API = "https://api.binance.com/api/v3/klines"
INTERVAL = "1m"
LIMIT = 120

TG_BOT_TOKEN = st.secrets.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = st.secrets.get("TG_CHAT_ID", "")

# =========================
# 工具
# =========================
def send_telegram(msg):
    if TG_BOT_TOKEN == "" or TG_CHAT_ID == "":
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TG_CHAT_ID, "text": msg})

def get_klines(symbol):
    params = {
        "symbol": symbol,
        "interval": INTERVAL,
        "limit": LIMIT
    }
    r = requests.get(BINANCE_API, params=params, timeout=10)
    data = r.json()
    df = pd.DataFrame(data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qav","trades",
        "taker_base","taker_quote","ignore"
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df[["open","high","low","close","volume"]] = \
        df[["open","high","low","close","volume"]].astype(float)
    return df

def add_indicators(df):
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["pct"] = df["close"].pct_change() * 100
    return df

# =========================
# Sniper 訊號（嚴格版）
# =========================
def sniper_signal(df):
    latest = df.iloc[-1]

    # 🔥 攻擊：放量 + 突破 + 價強
    attack = (
        latest["close"] > latest["ma20"] > latest["ma60"] and
        latest["volume"] > latest["vol_ma20"] * 2 and
        latest["pct"] > 0.8
    )

    # 💣 伏擊：爆量但價格未噴
    ambush = (
        latest["volume"] > latest["vol_ma20"] * 3 and
        abs(latest["pct"]) < 0.3
    )

    # 💀 出貨：跌破 + 爆量
    dump = (
        latest["close"] < latest["ma20"] and
        latest["volume"] > latest["vol_ma20"] * 2 and
        latest["pct"] < -1
    )

    return attack, ambush, dump

# =========================
# Streamlit UI
# =========================
st.set_page_config(page_title="Crypto Sniper", layout="wide")
st.title("🚀 Crypto Sniper｜虛擬貨幣戰情室")

symbols = st.sidebar.multiselect(
    "監控幣種",
    ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","AVAXUSDT"],
    default=["BTCUSDT","ETHUSDT"]
)

auto_refresh = st.sidebar.checkbox("🟢 啟動監控", True)
refresh_sec = st.sidebar.slider("刷新秒數", 10, 60, 20)

if "alert_log" not in st.session_state:
    st.session_state.alert_log = set()

cols = st.columns(len(symbols))

for col, symbol in zip(cols, symbols):
    with col:
        st.subheader(symbol)
        df = add_indicators(get_klines(symbol))

        attack, ambush, dump = sniper_signal(df)

        if attack:
            st.error("🔥 攻擊訊號")
            key = f"{symbol}_attack"
            if key not in st.session_state.alert_log:
                send_telegram(f"🔥【攻擊】{symbol}\n放量突破 + 價強")
                st.session_state.alert_log.add(key)

        if ambush:
            st.warning("💣 伏擊中")

        if dump:
            st.info("💀 出貨警告")
            key = f"{symbol}_dump"
            if key not in st.session_state.alert_log:
                send_telegram(f"💀【出貨】{symbol}\n跌破均線 + 爆量")
                st.session_state.alert_log.add(key)

        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=df["open_time"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"]
        ))
        fig.add_trace(go.Scatter(x=df["open_time"], y=df["ma20"], name="MA20"))
        fig.add_trace(go.Scatter(x=df["open_time"], y=df["ma60"], name="MA60"))
        st.plotly_chart(fig, use_container_width=True)

if auto_refresh:
    time.sleep(refresh_sec)
    st.rerun()