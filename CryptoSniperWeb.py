import streamlit as st
import pandas as pd
import numpy as np
import requests
import time
import plotly.graph_objects as go

# =========================
# 基本設定
# =========================
BINANCE_API = "https://api.binance.us/api/v3/klines"
INTERVAL = "1m"
LIMIT = 120
MIN_BARS = 60

TG_BOT_TOKEN = st.secrets.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = st.secrets.get("TG_CHAT_ID", "")

# =========================
# 工具函式
# =========================
def send_telegram(msg):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        requests.post(
            url,
            data={"chat_id": TG_CHAT_ID, "text": msg},
            timeout=5
        )
    except:
        pass


def get_klines(symbol):
    try:
        params = {
            "symbol": symbol,
            "interval": INTERVAL,
            "limit": LIMIT
        }
        r = requests.get(BINANCE_API, params=params, timeout=10)
        data = r.json()
        if not isinstance(data, list) or len(data) == 0:
            return None

        df = pd.DataFrame(data, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades",
            "taker_base", "taker_quote", "ignore"
        ])

        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        df[["open", "high", "low", "close", "volume"]] = \
            df[["open", "high", "low", "close", "volume"]].astype(float)

        return df
    except:
        return None


def add_indicators(df):
    df = df.copy()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["pct"] = df["close"].pct_change() * 100
    return df


# =========================
# Sniper 訊號（穩定版）
# =========================
def sniper_signal(df):
    if df is None or len(df) < MIN_BARS:
        return False, False, False

    latest = df.iloc[-1]

    if pd.isna(latest["ma20"]) or pd.isna(latest["ma60"]):
        return False, False, False

    attack = (
        latest["close"] > latest["ma20"] > latest["ma60"] and
        latest["volume"] > latest["vol_ma20"] * 2 and
        latest["pct"] > 0.8
    )

    ambush = (
        latest["volume"] > latest["vol_ma20"] * 3 and
        abs(latest["pct"]) < 0.3
    )

    dump = (
        latest["close"] < latest["ma20"] and
        latest["volume"] > latest["vol_ma20"] * 2 and
        latest["pct"] < -1
    )

    return attack, ambush, dump


# =========================
# Streamlit UI
# =========================
st.set_page_config(page_title="Crypto Sniper v1.1", layout="wide")
st.title("🚀 Crypto Sniper v1.1｜穩定版戰情室")

symbols = st.sidebar.multiselect(
    "監控幣種",
    ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT"],
    default=["BTCUSDT", "ETHUSDT"]
)

auto_refresh = st.sidebar.checkbox("🟢 啟動監控", True)
refresh_sec = st.sidebar.slider("刷新秒數", 15, 60, 20)

# Telegram 測試
st.sidebar.markdown("---")
if st.sidebar.button("📨 測試 Telegram"):
    send_telegram("✅ Crypto Sniper v1.1 測試成功")
    st.sidebar.success("已送出測試訊息")

# 防止重複通知
if "alert_log" not in st.session_state:
    st.session_state.alert_log = set()

cols = st.columns(len(symbols))

for col, symbol in zip(cols, symbols):
    with col:
        st.subheader(symbol)

        df = get_klines(symbol)
        st.write("DEBUG:", symbol, df is None, 0 if df is None else len(df))
        if df is None or len(df) < MIN_BARS:
            st.warning("⏳ 等待 K 線資料")
            continue

        df = add_indicators(df)
        attack, ambush, dump = sniper_signal(df)

        if attack:
            st.error("🔥 攻擊訊號")
            key = f"{symbol}_attack"
            if key not in st.session_state.alert_log:
                send_telegram(f"🔥【攻擊】{symbol}\n放量突破 + 價強")
                st.session_state.alert_log.add(key)

        if ambush:
            st.warning("💣 伏擊中（盤整）")

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
            close=df["close"],
            name="Price"
        ))
        fig.add_trace(go.Scatter(
            x=df["open_time"],
            y=df["ma20"],
            name="MA20"
        ))
        fig.add_trace(go.Scatter(
            x=df["open_time"],
            y=df["ma60"],
            name="MA60"
        ))
        fig.update_layout(height=400, margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, use_container_width=True)

if auto_refresh:
    time.sleep(refresh_sec)
    st.rerun()


