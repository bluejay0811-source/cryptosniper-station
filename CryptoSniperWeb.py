import streamlit as st
import pandas as pd
import numpy as np
import requests
import time
import plotly.graph_objects as go
from datetime import datetime, timedelta

# =========================
# 基本設定
# =========================
BINANCE_API = "https://api.binance.us/api/v3/klines"
OKX_API = "https://www.okx.com/api/v5/market/candles"
INTERVAL = "1m"
LIMIT = 120
MIN_BARS = 60

TG_BOT_TOKEN = st.secrets.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = st.secrets.get("TG_CHAT_ID", "")

# Grid 專用參數
GRID_PARAMS = {
    "BTCUSDT": {"lower_pct": 1.5, "upper_pct": 1.5, "grid_count": 20},
    "ETHUSDT": {"lower_pct": 2.0, "upper_pct": 2.0, "grid_count": 20},
    "SOLUSDT": {"lower_pct": 3.0, "upper_pct": 3.0, "grid_count": 15},
}

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


def get_klines_binance(symbol):
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


def get_klines_okx(symbol):
    """備援：OKX 行情"""
    try:
        # 轉換 symbol：BTCUSDT -> BTC-USDT
        okx_symbol = symbol.replace("USDT", "-USDT")
        params = {
            "instId": okx_symbol,
            "bar": "1m",
            "limit": LIMIT
        }
        r = requests.get(OKX_API, params=params, timeout=10)
        data = r.json()
        if data.get("code") != "0" or not data.get("data"):
            return None

        candles = data["data"]
        df = pd.DataFrame(candles, columns=[
            "open_time", "open", "high", "low", "close", "volume", "extra"
        ])

        df["open_time"] = pd.to_datetime(df["open_time"].astype(int), unit="ms")
        df[["open", "high", "low", "close", "volume"]] = \
            df[["open", "high", "low", "close", "volume"]].astype(float)

        return df.sort_values("open_time").reset_index(drop=True)
    except:
        return None


def get_klines(symbol):
    """主要行情源 + 自動備援"""
    df = get_klines_binance(symbol)
    if df is not None and len(df) > 0:
        return df, "Binance"

    df = get_klines_okx(symbol)
    if df is not None and len(df) > 0:
        return df, "OKX"

    return None, None


def add_indicators(df):
    df = df.copy()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["ma120"] = df["close"].rolling(120).mean()
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["pct"] = df["close"].pct_change() * 100

    # ATR 計算
    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            abs(df["high"] - df["close"].shift(1)),
            abs(df["low"] - df["close"].shift(1))
        )
    )
    df["atr"] = df["tr"].rolling(14).mean()
    df["atr_pct"] = (df["atr"] / df["close"]) * 100

    return df


# =========================
# 市場狀態判斷
# =========================
def market_state(df):
    """判斷趨勢 / 盤整 / 風險"""
    if df is None or len(df) < MIN_BARS:
        return "待命", None

    latest = df.iloc[-1]

    # 趨勢判斷
    if latest["close"] > latest["ma20"] > latest["ma60"] > latest["ma120"]:
        return "📈 上升趨勢", "UPTREND"
    elif latest["close"] < latest["ma20"] < latest["ma60"] < latest["ma120"]:
        return "📉 下降趨勢", "DOWNTREND"
    else:
        return "📊 盤整區", "RANGE"


# =========================
# Grid 建議計算
# =========================
def calculate_grid(symbol, df):
    """計算網格上下界 + 建議參數"""
    if df is None or len(df) < 20:
        return None

    latest = df.iloc[-1]
    current_price = latest["close"]
    atr_pct = latest["atr_pct"]

    if symbol not in GRID_PARAMS:
        return None

    params = GRID_PARAMS[symbol]
    lower_pct = params["lower_pct"]
    upper_pct = params["upper_pct"]
    grid_count = params["grid_count"]

    # 用 ATR 動態調整寬度
    atr_factor = min(atr_pct / 2.0, 2.0)  # 最多放大 2 倍
    lower_pct *= atr_factor
    upper_pct *= atr_factor

    lower_price = current_price * (1 - lower_pct / 100)
    upper_price = current_price * (1 + upper_pct / 100)

    grid_width = (upper_price - lower_price) / grid_count

    return {
        "current": current_price,
        "lower": lower_price,
        "upper": upper_price,
        "grid_count": grid_count,
        "grid_width": grid_width,
        "atr_pct": atr_pct
    }


# =========================
# Sniper 訊號（v2）
# =========================
def sniper_signal(df):
    if df is None or len(df) < MIN_BARS:
        return False, False, False

    latest = df.iloc[-1]

    if pd.isna(latest["ma20"]) or pd.isna(latest["ma60"]):
        return False, False, False

    # 🔥 攻擊：放量 + 突破 + 價強
    attack = (
        latest["close"] > latest["ma20"] > latest["ma60"] and
        latest["volume"] > latest["vol_ma20"] * 2 and
        latest["pct"] > 0.8
    )

    # 💣 伏擊：爆量但價格未動
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
# 風險雷達
# =========================
def risk_radar(df, symbol):
    """檢測急拉急殺"""
    if df is None or len(df) < 5:
        return None

    recent = df.tail(5)
    max_pct = recent["pct"].max()
    min_pct = recent["pct"].min()
    vol_spike = recent["volume"].iloc[-1] / recent["volume"].mean()

    alerts = []

    if max_pct > 2.0:
        alerts.append(f"🚀 急拉 {max_pct:.2f}%")

    if min_pct < -2.0:
        alerts.append(f"💥 急殺 {abs(min_pct):.2f}%")

    if vol_spike > 5:
        alerts.append(f"⚡ 爆量 {vol_spike:.1f}x")

    return alerts if alerts else None


# =========================
# Streamlit UI
# =========================
st.set_page_config(page_title="Crypto Sniper v2.0", layout="wide")
st.title("🚀 Crypto Sniper v2.0｜Grid Sniper 戰情室")

# Sidebar 設定
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
    msg = "✅ Crypto Sniper v2.0 Grid Sniper 上線！"
    send_telegram(msg)
    st.sidebar.success("測試訊息已發送")

st.sidebar.markdown("---")
st.sidebar.markdown("### 📊 Grid 參數設定")
st.sidebar.info("""
BTCUSDT: ±1.5% × ATR
ETHUSDT: ±2.0% × ATR
SOLUSDT: ±3.0% × ATR
""")

# 防止重複通知
if "alert_log" not in st.session_state:
    st.session_state.alert_log = {}

# 主顯示區
cols = st.columns(len(symbols))

for col, symbol in zip(cols, symbols):
    with col:
        st.subheader(symbol)

        df, source = get_klines(symbol)

        if df is None or len(df) < MIN_BARS:
            st.warning(f"⏳ 等待 K 線資料 ({source or '所有源'})")
            continue

        # 標記資料源
        if source:
            st.caption(f"📡 {source}")

        df = add_indicators(df)
        attack, ambush, dump = sniper_signal(df)
        state_text, state_code = market_state(df)

        # ========== 市場狀態 ==========
        st.metric("市場狀態", state_text)

        # ========== Grid 建議 ==========
        grid_info = calculate_grid(symbol, df)
        if grid_info:
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric(
                    "下界",
                    f"${grid_info['lower']:.2f}",
                    delta=f"-{GRID_PARAMS[symbol]['lower_pct']:.1f}%"
                )
            with col2:
                st.metric(
                    "現價",
                    f"${grid_info['current']:.2f}",
                    delta=df.iloc[-1]["pct"]
                )
            with col3:
                st.metric(
                    "上界",
                    f"${grid_info['upper']:.2f}",
                    delta=f"+{GRID_PARAMS[symbol]['upper_pct']:.1f}%"
                )

            with st.expander("📋 Grid 詳細參數"):
                st.write(f"""
**Grid 建議：**
- 網格數量：{grid_info['grid_count']} 格
- 單格寬度：${grid_info['grid_width']:.2f}
- 24h ATR：{grid_info['atr_pct']:.2f}%
                """)

        # ========== Sniper 訊號 ==========
        signal_cols = st.columns(3)
        with signal_cols[0]:
            if attack:
                st.error("🔥 攻擊訊號")
                key = f"{symbol}_attack_{datetime.now().strftime('%Y%m%d%H')}"
                if key not in st.session_state.alert_log:
                    grid_msg = ""
                    if grid_info:
                        grid_msg = f"""
上界：${grid_info['upper']:.2f}
下界：${grid_info['lower']:.2f}
"""
                    send_telegram(
                        f"🔥【攻擊】{symbol}\n"
                        f"放量突破 + 價強\n"
                        f"建議：收緊下界、偏多網格\n"
                        f"{grid_msg}"
                    )
                    st.session_state.alert_log[key] = True
            else:
                st.empty()

        with signal_cols[1]:
            if ambush:
                st.warning("💣 伏擊（爆量盤整）")
                st.caption("主力吸籌，擴大網格")
            else:
                st.empty()

        with signal_cols[2]:
            if dump:
                st.info("💀 出貨警告")
                key = f"{symbol}_dump_{datetime.now().strftime('%Y%m%d%H')}"
                if key not in st.session_state.alert_log:
                    send_telegram(
                        f"💀【出貨】{symbol}\n"
                        f"跌破均線 + 爆量\n"
                        f"建議：停網格或下移"
                    )
                    st.session_state.alert_log[key] = True

        # ========== 風險雷達 ==========
        risk = risk_radar(df, symbol)
        if risk:
            st.warning("⚠️ 風險警告")
            for r in risk:
                st.caption(r)
                # 大風險 Telegram 提醒
                if "急拉" in r or "急殺" in r:
                    key = f"{symbol}_risk_{datetime.now().strftime('%Y%m%d%H%M')}"
                    if key not in st.session_state.alert_log:
                        send_telegram(f"⚠️【{symbol}】{r}")
                        st.session_state.alert_log[key] = True

        # ========== K 線圖表 ==========
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
            name="MA20",
            line=dict(color="blue")
        ))
        fig.add_trace(go.Scatter(
            x=df["open_time"],
            y=df["ma60"],
            name="MA60",
            line=dict(color="orange")
        ))

        # 加上 Grid 參考線
        if grid_info:
            fig.add_hline(
                y=grid_info["lower"],
                line_dash="dash",
                line_color="red",
                annotation_text="下界",
                annotation_position="right"
            )
            fig.add_hline(
                y=grid_info["upper"],
                line_dash="dash",
                line_color="green",
                annotation_text="上界",
                annotation_position="right"
            )

        fig.update_layout(
            height=400,
            margin=dict(l=10, r=10, t=30, b=10),
            xaxis_rangeslider_visible=False
        )
        st.plotly_chart(fig, use_container_width=True)

if auto_refresh:
    time.sleep(refresh_sec)
    st.rerun()
