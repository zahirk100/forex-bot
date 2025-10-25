import os, re, json, time
from fastapi import FastAPI, Request, Header
from fastapi.responses import JSONResponse, PlainTextResponse
import pandas as pd
import numpy as np
import yfinance as yf

# ---- Alpaca config ----
from alpaca_trade_api import REST as ALPACA_REST
ALPACA_KEY_ID     = os.getenv("ALPACA_KEY_ID")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
MODE              = (os.getenv("MODE") or "alpaca_paper").strip().lower()
ALPACA_BASE_URL   = "https://paper-api.alpaca.markets" if "paper" in MODE else "https://api.alpaca.markets"

# Poe access key (vereist!)
ACCESS_KEY = os.getenv("ACCESS_KEY")

app = FastAPI()

# Alpaca client (alleen voor crypto orders)
alpaca = None
if ALPACA_KEY_ID and ALPACA_SECRET_KEY:
    alpaca = ALPACA_REST(ALPACA_KEY_ID, ALPACA_SECRET_KEY, base_url=ALPACA_BASE_URL)

# ---------- helpers ----------
def ok(data): return JSONResponse(data)
def err(msg): return JSONResponse({"error": str(msg)}, status_code=400)

def require_access(req: Request, access_key_header: str | None):
    if not ACCESS_KEY:
        return True  # geen check ingesteld
    return (access_key_header or "") == ACCESS_KEY

def format_money(x):
    try:
        return f"{float(x):,.2f}"
    except:
        return str(x)

def account_info():
    if not alpaca: return "Alpaca client niet geconfigureerd."
    a = alpaca.get_account()
    lines = [
        "📊 **Alpaca Paper account**",
        f"- Status: **{a.status.upper()}**",
        f"- Equity: {format_money(a.equity)}",
        f"- Cash: {format_money(a.cash)}",
        f"- Buying power: {format_money(a.buying_power)}",
    ]
    return "\n".join(lines)

def list_positions():
    if not alpaca: return "Alpaca client niet geconfigureerd."
    poss = alpaca.list_positions()
    if not poss: return "Geen open posities."
    out = ["**Open positions**"]
    for p in poss:
        if getattr(p, "asset_class", "crypto").lower() != "crypto":
            continue
        side = "LONG" if float(p.qty) > 0 else "SHORT"
        out.append(f"- {p.symbol} • {side} {p.qty} @ {p.avg_entry_price} (unrealized P/L {format_money(p.unrealized_pl)})")
    return "\n".join(out) if len(out) > 1 else "Geen open crypto-posities."

def place_crypto_order(symbol, side, notional_usd):
    if not alpaca: return "Alpaca client niet geconfigureerd."
    o = alpaca.submit_order(
        symbol=symbol,  # "BTC/USD"
        side=side,      # "buy" of "sell"
        type="market",
        notional=str(notional_usd),
        time_in_force="gtc"
    )
    return f"✅ Order geplaatst: **{side.upper()} {symbol}** voor ~${notional_usd}. Order id: `{o.id}`"

def price_btc():
    # haal laatste trade via positions of orders fallback; eenvoudiger: gebruik yfinance ook voor BTC
    try:
        data = yf.download("BTC-USD", period="1d", interval="1m", progress=False)
        last = float(data["Close"].dropna().iloc[-1])
        return f"BTC/USD ~ **${format_money(last)}**"
    except Exception as e:
        return f"Kon BTC prijs niet ophalen: {e}"

def price_eurusd():
    try:
        # Yahoo Finance forex ticker
        data = yf.download("EURUSD=X", period="1d", interval="1m", progress=False)
        last = float(data["Close"].dropna().iloc[-1])
        return f"EUR/USD ~ **{last:.5f}**"
    except Exception as e:
        return f"Kon EURUSD prijs niet ophalen: {e}"

def signal_eurusd():
    try:
        df = yf.download("EURUSD=X", period="1mo", interval="30m", progress=False)
        df = df.dropna()
        df["sma_fast"] = df["Close"].rolling(10).mean()
        df["sma_slow"] = df["Close"].rolling(30).mean()
        # RSI (14)
        delta = df["Close"].diff()
        up = delta.clip(lower=0)
        down = -1*delta.clip(upper=0)
        rs = (up.ewm(span=14, adjust=False).mean() / down.ewm(span=14, adjust=False).mean()).replace([np.inf, -np.inf], np.nan).fillna(0)
        df["rsi"] = 100 - (100 / (1 + rs))

        last = df.iloc[-1]
        price = float(last["Close"])
        cross_up = last["sma_fast"] > last["sma_slow"] and df["sma_fast"].iloc[-2] <= df["sma_slow"].iloc[-2]
        cross_dn = last["sma_fast"] < last["sma_slow"] and df["sma_fast"].iloc[-2] >= df["sma_slow"].iloc[-2]

        decision = "HOLD"
        if cross_up and last["rsi"] < 65: decision = "BUY"
        if cross_dn and last["rsi"] > 35: decision = "SELL"

        sl = price * (0.997 if decision == "BUY" else 1.003) if decision in ["BUY","SELL"] else None
        tp = price * (1.006 if decision == "BUY" else 0.994) if decision in ["BUY","SELL"] else None

        out = [
            "📈 **EURUSD signal**",
            f"- Price: {price:.5f}",
            f"- SMA10: {last['sma_fast']:.5f} | SMA30: {last['sma_slow']:.5f}",
            f"- RSI(14): {last['rsi']:.1f}",
            f"- Decision: **{decision}**",
        ]
        if sl and tp:
            out.append(f"- SL: {sl:.5f} | TP: {tp:.5f}")
        return "\n".join(out)
    except Exception as e:
        return f"Kon signal niet berekenen: {e}"

HELP_TEXT = (
    "**Commands**\n"
    "- `account` – status en buying power\n"
    "- `positions` – open posities\n"
    "- `price btc` / `price eurusd` – laatste prijs\n"
    "- `buy btc <notional_usd>` – marktkoop ($ bedrag), bv `buy btc 25`\n"
    "- `sell btc <notional_usd>` – marktverkoop ($ bedrag)\n"
    "- `signal eurusd` – SMA/RSI signaal (alleen signal, geen order)\n"
)

# ---------- routes ----------
@app.get("/")
def root():
    return {"status": "ok", "mode": MODE}

@app.get("/debug")
def debug(request: Request, authorization: str | None = Header(default=None),
          poe_access_key: str | None = Header(default=None), x_access_key: str | None = Header(default=None),
          user_agent: str | None = Header(default=None)):
    return PlainTextResponse(json.dumps({
        "has_ACCESS_KEY": ACCESS_KEY is not None,
        "received_keys": {"authorization": authorization, "poe-access-key": poe_access_key, "x-access-key": x_access_key},
        "user-agent": user_agent
    }))

@app.post("/webhook")
async def webhook(request: Request,
                  authorization: str | None = Header(default=None),
                  poe_access_key: str | None = Header(default=None),
                  x_access_key: str | None = Header(default=None)):
    if not require_access(request, authorization or poe_access_key or x_access_key):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    body = await request.json()
    # Poe stuurt het user-bericht in body["messages"][-1]["content"][0]["text"]
    try:
        messages = body.get("messages") or []
        last = messages[-1]
        parts = last.get("content", [])
        text = ""
        for p in parts:
            if p.get("type") == "text":
                text += p.get("text", "")
        q = (text or "").strip().lower()
    except Exception:
        q = ""

    # parsing
    if q in ["help", "commands", "?"]:
        return ok({"text": HELP_TEXT})
    if q.startswith("account"):
        return ok({"text": account_info()})
    if q.startswith("positions"):
        return ok({"text": list_positions()})
    if q.startswith("price"):
        if "eurusd" in q:  return ok({"text": price_eurusd()})
        return ok({"text": price_btc()})
    if q.startswith("signal") and "eurusd" in q:
        return ok({"text": signal_eurusd()})
    m = re.match(r"(buy|sell)\s+btc\s+([0-9]+(?:\.[0-9]+)?)", q)
    if m:
        side = m.group(1)
        notional = float(m.group(2))
        try:
            msg = place_crypto_order("BTC/USD", side, notional)
            return ok({"text": msg})
        except Exception as e:
            return ok({"text": f"Order fout: {e}"})

    # fallback
    return ok({"text": HELP_TEXT})
