import os
import time
import hmac
import hashlib
import requests
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()

# ---------- Config uit omgevingsvariabelen ----------
ACCESS_KEY = os.getenv("KEY", "")  # Poe access key (zelfde als in Poe settings)
ALPACA_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET = os.getenv("ALPACA_API_SECRET", "")
ALPACA_PAPER = os.getenv("MODE", "alpaca_paper").strip()  # "alpaca_paper" of "alpaca_live"
SYMBOL = os.getenv("TRADE_SYMBOL", "BTC/USD")
RISK_PCT = float(os.getenv("RISK_PCT", "50"))
MAX_POS_USD = float(os.getenv("MAX_POS_USD", "200"))
TIMEFRAME = os.getenv("TIMEFRAME", "1Min")
AUTO = os.getenv("ENABLE_AUTO", "true").lower() == "true"

ALPACA_BASE = "https://paper-api.alpaca.markets" if ALPACA_PAPER.startswith("alpaca_paper") \
              else "https://api.alpaca.markets"
ALPACA_DATA = "https://data.alpaca.markets/v2"

def alpaca_headers():
    return {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json",
    }

# ---------- Health & Debug ----------
@app.get("/")
def root():
    return {"status": "ok", "mode": ALPACA_PAPER}

@app.get("/debug")
async def debug(request: Request):
    return {
        "has_KEY": bool(ACCESS_KEY),
        "mode": ALPACA_PAPER,
        "symbol": SYMBOL,
        # headers zie je in browser niet; Poe stuurt ze wel mee
        "received_keys": {
            "authorization": request.headers.get("authorization"),
            "poe-access-key": request.headers.get("poe-access-key"),
            "x-access-key": request.headers.get("x-access-key"),
        },
    }

# ---------- Alpaca helpers ----------
def get_account():
    r = requests.get(f"{ALPACA_BASE}/v2/account", headers=alpaca_headers(), timeout=20)
    r.raise_for_status()
    return r.json()

def get_last_price(symbol: str):
    # Voor crypto gebruikt Alpaca symbool-stijl: BTC/USD
    sym = symbol.replace("-", "/").upper()
    r = requests.get(f"{ALPACA_DATA}/stocks/{sym}/trades/latest", headers=alpaca_headers(), timeout=20)
    # Fallback naar crypto endpoint als stocks faalt
    if r.status_code >= 400:
        r = requests.get(f"{ALPACA_DATA}/crypto/{sym}/trades/latest", headers=alpaca_headers(), timeout=20)
    r.raise_for_status()
    j = r.json()
    # Probeer meerdere velden (API’s variëren)
    price = None
    if isinstance(j, dict):
        price = (
            j.get("trade", {}).get("p")
            or j.get("latestTrade", {}).get("p")
            or j.get("price")
        )
    if price is None:
        raise RuntimeError(f"Geen prijsveld in {j}")
    return float(price)

def position_size_usd(equity_usd: float) -> float:
    return min(equity_usd * (RISK_PCT / 100.0), MAX_POS_USD)

def place_market_order(symbol: str, notional_usd: float, side: str):
    payload = {
        "symbol": symbol.replace("-", "/").upper(),
        "notional": round(notional_usd, 2),
        "side": side,          # "buy" of "sell"
        "type": "market",
        "time_in_force": "gtc",
    }
    r = requests.post(f"{ALPACA_BASE}/v2/orders", headers=alpaca_headers(), json=payload, timeout=20)
    if r.status_code >= 400:
        raise HTTPException(status_code=500, detail=f"Orderfout: {r.status_code} {r.text}")
    return r.json()

# ---------- Poe webhook ----------
def _check_key(request: Request):
    k = (
        request.headers.get("poe-access-key")
        or request.headers.get("x-access-key")
        or request.headers.get("authorization")
    )
    if ACCESS_KEY:
        if not k or k.strip() != ACCESS_KEY.strip():
            raise HTTPException(status_code=403, detail="Forbidden (bad access key)")

@app.post("/webhook")
async def webhook(request: Request):
    _check_key(request)
    data = await request.json()
    text = (data.get("text") or "").strip().lower()

    # 1) Account info
    if text in ("account", "acc", "balance"):
        try:
            acc = get_account()
            reply = (
                f"📊 Alpaca Paper account\n"
                f"• Status: {acc.get('status')}\n"
                f"• Equity: {acc.get('equity')}\n"
                f"• Cash: {acc.get('cash')}\n"
                f"• Buying power: {acc.get('buying_power','-')}\n"
                f"• Symbol: {SYMBOL}  | Auto: {AUTO}"
            )
            return {"type": "text", "text": reply}
        except Exception as e:
            return {"type": "text", "text": f"Account error: {e}"}

    # 2) Koop / Verkoop commando
    if text.startswith("buy") or text.startswith("koop"):
        try:
            acc = get_account()
            notional = position_size_usd(float(acc["equity"]))
            order = place_market_order(SYMBOL, notional, "buy")
            price = get_last_price(SYMBOL)
            return {"type": "text", "text": f"✅ BUY {SYMBOL} ~${notional} @ ~{price}\nOrder: {order.get('id')}"}
        except Exception as e:
            return {"type": "text", "text": f"Buy error: {e}"}

    if text.startswith("sell") or text.startswith("verkoop"):
        try:
            acc = get_account()
            notional = position_size_usd(float(acc["equity"]))
            order = place_market_order(SYMBOL, notional, "sell")
            price = get_last_price(SYMBOL)
            return {"type": "text", "text": f"✅ SELL {SYMBOL} ~${notional} @ ~{price}\nOrder: {order.get('id')}"}
        except Exception as e:
            return {"type": "text", "text": f"Sell error: {e}"}

    # 3) Onbekend commando
    help_txt = (
        "Beschikbare commando’s:\n"
        "• account – laat saldo/vermogen zien\n"
        "• buy / koop – koop marktorder (risico-param)\n"
        "• sell / verkoop – verkoop marktorder\n"
    )
    return {"type": "text", "text": help_txt}
