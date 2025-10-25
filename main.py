import os, time, asyncio, json, uuid, logging, math
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import requests
from fastapi import FastAPI, Request, Response
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator

# ------------- Config -------------
ALPACA_API_BASE = os.getenv("ALPACA_API_BASE", "https://paper-api.alpaca.markets").rstrip("/")
ALPACA_DATA_BASE = os.getenv("ALPACA_DATA_BASE", "https://data.alpaca.markets").rstrip("/")
API_KEY = os.getenv("ALPACA_API_KEY_ID")
API_SECRET = os.getenv("ALPACA_API_SECRET_KEY")
MODE = os.getenv("MODE", "alpaca_paper")
SYMBOLS = [s.strip() for s in os.getenv("TRADE_SYMBOLS", "BTC/USD").split(",") if s.strip()]
RISK_USD = float(os.getenv("RISK_PER_TRADE_USD", "50"))
MAX_POS_USD = float(os.getenv("MAX_POSITION_USD", "200"))
TIMEFRAME = os.getenv("TIMEFRAME", "1Min")
ENABLE_TRADING = os.getenv("ENABLE_TRADING", "true").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
log = logging.getLogger("trader")

HEADERS = {
    "APCA-API-KEY-ID": API_KEY or "",
    "APCA-API-SECRET-KEY": API_SECRET or "",
    "Content-Type": "application/json",
}

app = FastAPI()

# ------------- Helpers -------------
def _now():
    return datetime.now(timezone.utc).isoformat()

def alpaca_get(path, params=None, data_api=False):
    base = ALPACA_DATA_BASE if data_api else ALPACA_API_BASE
    url = f"{base}{path}"
    r = requests.get(url, headers=HEADERS, params=params, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"GET {path} {r.status_code} {r.text}")
    return r.json()

def alpaca_post(path, payload):
    url = f"{ALPACA_API_BASE}{path}"
    r = requests.post(url, headers=HEADERS, data=json.dumps(payload), timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"POST {path} {r.status_code} {r.text}")
    return r.json()

def alpaca_delete(path):
    url = f"{ALPACA_API_BASE}{path}"
    r = requests.delete(url, headers=HEADERS, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"DELETE {path} {r.status_code} {r.text}")
    return r.json() if r.text else {}

def get_account():
    return alpaca_get("/v2/account")

def get_position(symbol):
    try:
        return alpaca_get(f"/v2/positions/{symbol.replace('/','%2F')}")
    except Exception:
        return None

def get_open_orders(symbol=None):
    params = {"status": "open"}
    if symbol:
        params["symbols"] = symbol
    return alpaca_get("/v2/orders", params=params)

def cancel_all_orders():
    try:
        alpaca_delete("/v2/orders")
    except Exception as e:
        log.warning(f"Cancel orders error: {e}")

def place_market_notional(symbol, side, notional_usd):
    payload = {
        "symbol": symbol,
        "side": side,
        "type": "market",
        "time_in_force": "gtc",
        "notional": round(float(notional_usd), 2),
        "client_order_id": f"auto-{uuid.uuid4().hex[:12]}",
    }
    return alpaca_post("/v2/orders", payload)

def close_position(symbol):
    try:
        return alpaca_delete(f"/v2/positions/{symbol.replace('/','%2F')}")
    except Exception as e:
        log.info(f"No position to close for {symbol}: {e}")
        return {}

def bars_crypto(symbol, tf="1Min", limit=200):
    # v1beta3 crypto US bars
    params = {"symbols": symbol, "timeframe": tf, "limit": limit}
    j = alpaca_get("/v1beta3/crypto/us/bars", params=params, data_api=True)
    items = j.get("bars", {}).get(symbol, [])
    if not items:
        raise RuntimeError(f"No bars for {symbol}")
    df = pd.DataFrame(items)
    # Ensure numeric
    for col in ["o", "h", "l", "c", "v"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # Standardize column names
    df.rename(columns={"t":"ts","o":"open","h":"high","l":"low","c":"close","v":"volume"}, inplace=True)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df.set_index("ts", inplace=True)
    return df.sort_index()

# ------------- Strategy -------------
def signal_ema_rsi(df: pd.DataFrame):
    """Return 'buy', 'sell', or None based on EMA20/EMA50 + RSI filter."""
    if len(df) < 60:
        return None
    ema_fast = EMAIndicator(close=df["close"], window=20).ema_indicator()
    ema_slow = EMAIndicator(close=df["close"], window=50).ema_indicator()
    rsi = RSIIndicator(close=df["close"], window=14).rsi()

    df = df.copy()
    df["ema_fast"] = ema_fast
    df["ema_slow"] = ema_slow
    df["rsi"] = rsi

    last = df.iloc[-1]
    prev = df.iloc[-2]

    cross_up = prev["ema_fast"] <= prev["ema_slow"] and last["ema_fast"] > last["ema_slow"]
    cross_dn = prev["ema_fast"] >= prev["ema_slow"] and last["ema_fast"] < last["ema_slow"]

    if cross_up and last["rsi"] > 55:
        return "buy"
    if cross_dn or last["rsi"] < 45:
        return "sell"
    return None

async def trade_symbol(symbol: str):
    try:
        df = bars_crypto(symbol, TIMEFRAME, 300)
        sig = signal_ema_rsi(df)
        acct = get_account()
        bp = float(acct.get("buying_power", 0))
        log.info(f"[{symbol}] signal={sig} bp={bp:.2f}")

        pos = get_position(symbol)
        has_long = pos and float(pos.get("qty", 0)) > 0

        if not ENABLE_TRADING:
            log.info("Trading disabled; skipping order logic.")
            return

        # Don’t exceed cap
        if pos:
            market_value = float(pos.get("market_value", 0))
        else:
            market_value = 0.0

        if sig == "buy" and not has_long and bp > RISK_USD and (market_value + RISK_USD) <= MAX_POS_USD:
            cancel_all_orders()
            r = place_market_notional(symbol, "buy", RISK_USD)
            log.info(f"BUY {symbol} notional ${RISK_USD}: {r.get('id')}")
        elif sig == "sell" and has_long:
            cancel_all_orders()
            r = close_position(symbol)
            log.info(f"CLOSE {symbol}: {r}")
    except Exception as e:
        log.error(f"trade_symbol error for {symbol}: {e}")

async def trading_loop():
    await asyncio.sleep(3)  # give server a moment to boot
    log.info(f"Trading loop started at {_now()}, symbols={SYMBOLS}, tf={TIMEFRAME}, enable={ENABLE_TRADING}")
    while True:
        tasks = [trade_symbol(sym) for sym in SYMBOLS]
        await asyncio.gather(*tasks)
        await asyncio.sleep(60)  # run every minute

# ------------- FastAPI -------------
@app.on_event("startup")
async def _startup():
    # Start background trader
    asyncio.create_task(trading_loop())

@app.get("/")
def root():
    return {"status": "ok", "mode": MODE}

@app.get("/health")
def health():
    return {"ok": True, "time": _now()}

@app.get("/status")
def status():
    try:
        acct = get_account()
        return {
            "status": "ok",
            "equity": float(acct.get("equity", 0)),
            "cash": float(acct.get("cash", 0)),
            "buying_power": float(acct.get("buying_power", 0)),
            "trading_enabled": ENABLE_TRADING,
            "symbols": SYMBOLS,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

# Minimal Poe-compatible webhook (POST only)
@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = (body.get("message", "") or body.get("text", "") or "").strip().lower()
    if text in ["account", "status"]:
        a = get_account()
        msg = (f"📊 Alpaca Paper account\n"
               f"• Status: {a.get('status','?').upper()}\n"
               f"• Equity: {a.get('equity','?')}\n"
               f"• Cash: {a.get('cash','?')}\n"
               f"• Buying power: {a.get('buying_power','?')}")
        return {"text": msg}
    elif text in ["positions", "pos"]:
        out = []
        try:
            pos = alpaca_get("/v2/positions")
            for p in pos:
                out.append(f"{p['symbol']}: qty {p['qty']} mv ${p['market_value']}")
        except Exception:
            out.append("No positions")
        return {"text": "📦 Positions\n" + ("\n".join(out) if out else "No positions")}
    else:
        return {"text": "Try: `account` or `positions`"}
