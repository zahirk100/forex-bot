import os, time, asyncio, json, logging, math
from datetime import datetime, timezone, timedelta
import requests
import pandas as pd
import numpy as np

from fastapi import FastAPI, Request, Response

# ---------- Config uit ENV ----------
ALPACA_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET = os.getenv("ALPACA_API_SECRET", "")
ALPACA_PAPER = os.getenv("MODE", "alpaca_paper").strip()
ACCESS_KEY = os.getenv("KEY", "")  # Poe/own webhook key

SYMBOL = os.getenv("TRADE_SYMBOL", "BTC/USD").strip()
TIMEFRAME = os.getenv("TIMEFRAME", "1Min").strip()      # 1Min, 5Min, 15Min
RISK_PCT = float(os.getenv("RISK_PCT", "0.5"))           # % van equity per trade
MAX_POSITION_NOTIONAL = float(os.getenv("MAX_POSITION_NOTIONAL", "200"))
TP_R = float(os.getenv("TP_R", "1.5"))                   # take profit bij 1.5R
SL_ATR_MULT = float(os.getenv("SL_ATR_MULT", "1.5"))
ENABLE_TRADING = os.getenv("ENABLE_TRADING", "true").lower() == "true"
LOOP_SECONDS = int(os.getenv("LOOP_SECONDS", "60"))

# ---------- Endpoints Alpaca ----------
TRADE_BASE = "https://paper-api.alpaca.markets" if "paper" in ALPACA_PAPER else "https://api.alpaca.markets"
DATA_BASE  = "https://data.alpaca.markets"

HEADERS = {
    "APCA-API-KEY-ID": ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
}

# ---------- Helpers ----------
app = FastAPI()
log = logging.getLogger("bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def sym_for_position(sym: str) -> str:
    """Alpaca positions/orders gebruiken BTCUSD in plaats van BTC/USD."""
    return sym.replace("/", "")


def fetch_bars(symbol: str, timeframe: str, limit: int = 400) -> pd.DataFrame:
    # v1beta3 Crypto US bars
    url = f"{DATA_BASE}/v1beta3/crypto/us/bars"
    params = {"symbols": symbol, "timeframe": timeframe, "limit": limit}
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    raw = r.json()
    bars = raw.get("bars", {}).get(symbol, [])
    if not bars:
        raise ValueError("No bars returned")
    df = pd.DataFrame(bars)
    # kolommen: t,o,h,l,c,v
    df["t"] = pd.to_datetime(df["t"])
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df.set_index("t", inplace=True)
    return df


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(n).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(n).mean()
    rs = gain / (loss.replace(0, np.nan))
    return 100 - (100 / (1 + rs))


def signal_from_indicators(df: pd.DataFrame) -> str:
    """Return 'long', 'short' of 'flat'."""
    fast = ema(df["close"], 12)
    slow = ema(df["close"], 26)
    _rsi = rsi(df["close"], 14)
    _atr = atr(df, 14)
    df = df.copy()
    df["fast"], df["slow"], df["rsi"], df["atr"] = fast, slow, _rsi, _atr

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Crossover + RSI filter
    crossed_up = prev["fast"] <= prev["slow"] and last["fast"] > last["slow"] and last["rsi"] > 52
    crossed_dn = prev["fast"] >= prev["slow"] and last["fast"] < last["slow"] and last["rsi"] < 48

    if crossed_up:
        return "long"
    if crossed_dn:
        return "short"
    return "flat"


def get_account():
    r = requests.get(f"{TRADE_BASE}/v2/account", headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def get_position(symbol: str):
    s = sym_for_position(symbol)
    r = requests.get(f"{TRADE_BASE}/v2/positions/{s}", headers=HEADERS, timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def close_position(symbol: str):
    s = sym_for_position(symbol)
    r = requests.delete(f"{TRADE_BASE}/v2/positions/{s}", headers=HEADERS, timeout=20)
    if r.status_code in (200, 204):
        return {"closed": True}
    return {"closed": False, "detail": r.text}


def place_order_market(symbol: str, side: str, notional: float, sl_price: float = None, tp_price: float = None):
    payload = {
        "symbol": sym_for_position(symbol),
        "side": side,
        "type": "market",
        "notional": round(notional, 2),
        "time_in_force": "gtc",
    }
    if sl_price:
        payload["stop_loss"] = {"stop_price": round(sl_price, 2)}
    if tp_price:
        payload["take_profit"] = {"limit_price": round(tp_price, 2)}

    r = requests.post(f"{TRADE_BASE}/v2/orders", headers=HEADERS, json=payload, timeout=30)
    if r.status_code >= 300:
        return {"ok": False, "detail": r.text}
    return {"ok": True, **r.json()}


def compute_position_size(price: float, atr_val: float, equity: float) -> float:
    """Return notional (USD) om ~RISK_PCT % te riskeren met SL_ATR_MULT * ATR afstand."""
    if atr_val <= 0 or price <= 0:
        return min(MAX_POSITION_NOTIONAL, equity * (RISK_PCT / 100.0))
    stop_dist = SL_ATR_MULT * atr_val
    risk_usd = equity * (RISK_PCT / 100.0)
    # benadering: notional zodat 1 * stop_dist ~ risk_usd
    # qty = risk_usd / stop_dist  → notional = qty * price
    qty = max(risk_usd / stop_dist, 0)
    notional = qty * price
    notional = min(notional, MAX_POSITION_NOTIONAL)
    if notional < 10:
        notional = min(MAX_POSITION_NOTIONAL, 50.0)  # floor
    return float(notional)


async def strategy_once():
    if not ENABLE_TRADING:
        return {"enabled": False}
    try:
        df = fetch_bars(SYMBOL, TIMEFRAME, limit=200)
        sig = signal_from_indicators(df)
        last = df.iloc[-1]
        atr_val = atr(df, 14).iloc[-1]
        price = float(last["close"])

        pos = get_position(SYMBOL)
        have_pos = pos is not None
        side_pos = None
        if have_pos:
            side_pos = "long" if float(pos["qty"]) > 0 and pos["side"] == "long" else "short"

        if sig == "long" and not have_pos:
            acc = get_account()
            equity = float(acc["equity"])
            notional = compute_position_size(price, atr_val, equity)
            sl = price - SL_ATR_MULT * atr_val
            tp = price + TP_R * (price - sl)
            res = place_order_market(SYMBOL, "buy", notional, sl_price=sl, tp_price=tp)
            return {"action": "buy", "ok": res.get("ok", False), "detail": res.get("detail"), "notional": notional}

        if sig == "short" and not have_pos:
            acc = get_account()
            equity = float(acc["equity"])
            notional = compute_position_size(price, atr_val, equity)
            # Voor korte posities in crypto via Alpaca: gebruik "sell" market (Alpaca support short crypto)
            sl = price + SL_ATR_MULT * atr_val
            tp = price - TP_R * (sl - price)
            res = place_order_market(SYMBOL, "sell", notional, sl_price=sl, tp_price=tp)
            return {"action": "sell", "ok": res.get("ok", False), "detail": res.get("detail"), "notional": notional}

        # Reversal/exit: kruist terug tegen onze positie → sluit alles
        if have_pos:
            fast = ema(df["close"], 12).iloc[-1]
            slow = ema(df["close"], 26).iloc[-1]
            if side_pos == "long" and fast < slow:
                c = close_position(SYMBOL)
                return {"action": "close_long", **c}
            if side_pos == "short" and fast > slow:
                c = close_position(SYMBOL)
                return {"action": "close_short", **c}

        return {"action": "hold", "signal": sig}
    except Exception as e:
        log.exception("strategy_once error")
        return {"error": str(e)}


# ---------- FastAPI ----------
@app.get("/")
def root():
    return {"status": "ok", "mode": ALPACA_PAPER}


def _auth_ok(req: Request):
    # Poe zet vaak header 'poe-access-key' of 'authorization: Bearer ...'
    auth = req.headers.get("poe-access-key") or req.headers.get("x-access-key")
    if not auth:
        auth_hdr = req.headers.get("authorization", "")
        if auth_hdr.lower().startswith("bearer "):
            auth = auth_hdr.split(" ", 1)[1]
    return (ACCESS_KEY == "") or (auth == ACCESS_KEY)


@app.post("/webhook")
async def webhook(request: Request):
    if not _auth_ok(request):
        return Response(content=json.dumps({"error": "forbidden"}), media_type="application/json", status_code=403)

    try:
        body = await request.json()
    except Exception:
        body = {}

    # Poe stuurt text meestal onder `message` of `query` – fallback naar raw
    text = (body.get("message") or body.get("query") or "").strip().lower()

    if text in ("account", "acc", "balance"):
        acc = get_account()
        reply = f"📊 Alpaca Paper account\n• Status: {acc['status']}\n• Equity: {acc['equity']}\n• Cash: {acc['cash']}\n• Buying power: {acc.get('buying_power','-')}"
        return {"type": "text", "text": reply}

    if text in ("status", "pos", "position"):
        pos = get_position(SYMBOL)
        if pos:
            reply = f"📌 Position {SYMBOL.replace('/','')}: {pos['side']} qty={pos['qty']} avg={pos['avg_entry_price']}"
        else:
            reply = f"📌 Geen open positie in {SYMBOL}"
        return {"type": "text", "text": reply}

    if text in ("buy", "long"):
        acc = get_account()
        df = fetch_bars(SYMBOL, TIMEFRAME, limit=60)
        price = float(df["close"].iloc[-1])
        atr_val = atr(df, 14).iloc[-1]
        notional = compute_position_size(price, atr_val, float(acc["equity"]))
        sl = price - SL_ATR_MULT * atr_val
        tp = price + TP_R * (price - sl)
        res = place_order_market(SYMBOL, "buy", notional, sl_price=sl, tp_price=tp)
        return {"type": "text", "text": f"✅ BUY {SYMBOL} notional≈${notional:.2f} | ok={res.get('ok')} {res.get('detail','')}"}

    if text in ("sell", "short"):
        acc = get_account()
        df = fetch_bars(SYMBOL, TIMEFRAME, limit=60)
        price = float(df["close"].iloc[-1])
        atr_val = atr(df, 14).iloc[-1]
        notional = compute_position_size(price, atr_val, float(acc["equity"]))
        sl = price + SL_ATR_MULT * atr_val
        tp = price - TP_R * (sl - price)
        res = place_order_market(SYMBOL, "sell", notional, sl_price=sl, tp_price=tp)
        return {"type": "text", "text": f"✅ SELL {SYMBOL} notional≈${notional:.2f} | ok={res.get('ok')} {res.get('detail','')}"}

    if text in ("close", "exit"):
        res = close_position(SYMBOL)
        return {"type": "text", "text": f"🔚 Close: {res}"}

    if text in ("enable", "disable"):
        global ENABLE_TRADING
        ENABLE_TRADING = (text == "enable")
        return {"type": "text", "text": f"Auto-trading: {ENABLE_TRADING}"}

    # default: run 1 strategie-tick
    res = await strategy_once()
    return {"type": "text", "text": json.dumps(res)}


# ---------- Achtergrond loop ----------
async def loop_task():
    await asyncio.sleep(5)
    while True:
        try:
            if ENABLE_TRADING:
                r = await strategy_once()
                log.info(f"loop tick: {r}")
        except Exception:
            log.exception("loop error")
        await asyncio.sleep(LOOP_SECONDS)


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(loop_task())


# Render boot: uvicorn main:app --host 0.0.0.0 --port 10000
