import os
import re
import json
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, Request, Header
from fastapi.responses import JSONResponse

app = FastAPI()

# ---------- helpers ----------

def poe_text(msg: str) -> JSONResponse:
    """
    Minimaal geldig antwoord voor Poe server-bots.
    Poe verwacht exact dit schema.
    """
    return JSONResponse(
        status_code=200,
        content={
            "type": "message",
            "message": {
                "role": "bot",
                "content": [
                    {"type": "text", "text": msg}
                ]
            }
        },
        headers={"Content-Type": "application/json"}
    )

def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)

def get_alpaca_headers() -> Dict[str, str]:
    # Ondersteun meerdere namen voor jouw ENV variabelen
    key = env("ALPACA_PAPER_KEY") or env("ALPACA_KEY") or env("ALPACA_API_KEY") or ""
    secret = env("ALPACA_PAPER_SECRET") or env("ALPACA_SECRET") or env("ALPACA_API_SECRET") or ""
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

def get_alpaca_base() -> str:
    # Standaard paper endpoint
    return env("ALPACA_PAPER_ENDPOINT", "https://paper-api.alpaca.markets").rstrip("/")

def alpaca_get(path: str) -> Dict[str, Any]:
    url = f"{get_alpaca_base()}{path}"
    r = requests.get(url, headers=get_alpaca_headers(), timeout=15)
    r.raise_for_status()
    return r.json()

def alpaca_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{get_alpaca_base()}{path}"
    r = requests.post(url, headers=get_alpaca_headers(), json=payload, timeout=15)
    r.raise_for_status()
    return r.json()

def parse_user_text(body: Dict[str, Any]) -> str:
    """
    Poe stuurt berichten als body["messages"][-1]["content"][0]["text"]
    """
    try:
        msgs: List[Dict[str, Any]] = body.get("messages", [])
        if not msgs:
            return ""
        last = msgs[-1]
        parts = last.get("content", [])
        if parts and parts[0].get("type") == "text":
            return parts[0].get("text", "")
    except Exception:
        pass
    return ""

def format_money(x: Any) -> str:
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return str(x)

# ---------- routes ----------

@app.get("/")
def root():
    return {"status": "ok", "mode": env("MODE", "alpaca_paper")}

@app.get("/keys")
def keys(request: Request):
    # Debug endpoint om headers te zien (niet door Poe gebruikt)
    hdrs = dict(request.headers)
    return {
        "has_ACCESS_KEY": bool(env("ACCESS_KEY", "")),
        "received_keys": {
            "authorization": hdrs.get("authorization"),
            "poe-access-key": hdrs.get("poe-access-key"),
            "x-access-key": hdrs.get("x-access-key"),
            "user-agent": hdrs.get("user-agent"),
        },
    }

@app.post("/webhook")
async def webhook(
    request: Request,
    poe_access_key: Optional[str] = Header(default=None, alias="Poe-Access-Key"),
    x_access_key: Optional[str] = Header(default=None, alias="x-access-key"),
):
    body = await request.json()

    # 1) Access-key check (alleen als je ACCESS_KEY gezet hebt in Render)
    expected = env("ACCESS_KEY", "")
    if expected:
        provided = poe_access_key or x_access_key or (request.headers.get("poe-access-key") or request.headers.get("x-access-key"))
        if provided != expected:
            return JSONResponse(status_code=403, content={"error": "ACCESS_KEY_INVALID_OR_MISSING"})

    # 2) Tekst uit het user-bericht
    user_text = parse_user_text(body).strip()
    if not user_text:
        return poe_text("Ik ontving geen tekstbericht. Probeer bijvoorbeeld: 'account', 'positions', 'buy AAPL 1'.")

    # 3) Heel simpele command parser
    low = user_text.lower()

    # a) account info
    if low.startswith("account"):
        try:
            data = alpaca_get("/v2/account")
            equity = format_money(data.get("equity"))
            cash = format_money(data.get("cash"))
            bp = format_money(data.get("buying_power"))
            st = data.get("status")
            return poe_text(f"Alpaca (paper) account status: **{st}**\nEquity: {equity}\nCash: {cash}\nBuying power: {bp}")
        except requests.HTTPError as e:
            try:
                err = e.response.json()
            except Exception:
                err = {"message": str(e)}
            return poe_text(f"Kon account niet ophalen 🛠️\n{json.dumps(err, ensure_ascii=False)}")

    # b) positions
    if low.startswith("positions"):
        try:
            rows = alpaca_get("/v2/positions")
            if not rows:
                return poe_text("Geen open positions.")
            lines = []
            for p in rows:
                sym = p.get("symbol")
                qty = p.get("qty")
                side = p.get("side")
                market_value = format_money(p.get("market_value"))
                unrealized_pl = format_money(p.get("unrealized_pl"))
                lines.append(f"{sym}: {qty} ({side}), MV {market_value}, P/L {unrealized_pl}")
            return poe_text("Open positions:\n" + "\n".join(lines))
        except requests.HTTPError as e:
            try:
                err = e.response.json()
            except Exception:
                err = {"message": str(e)}
            return poe_text(f"Kon positions niet ophalen 🛠️\n{json.dumps(err, ensure_ascii=False)}")

    # c) buy/sell <SYMBOL> <QTY>
    m = re.match(r"^(buy|sell)\s+([A-Za-z\.]+)\s+(\d+)$", low)
    if m:
        side, symbol, qty = m.group(1), m.group(2).upper(), int(m.group(3))
        try:
            order = alpaca_post("/v2/orders", {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "type": "market",
                "time_in_force": "day"
            })
            oid = order.get("id")
            filled = order.get("filled_qty", "0")
            status = order.get("status")
            return poe_text(f"Order geplaatst: {side.upper()} {qty} {symbol}\nStatus: {status}, filled: {filled}\nOrder id: {oid}")
        except requests.HTTPError as e:
            try:
                err = e.response.json()
            except Exception:
                err = {"message": str(e)}
            return poe_text(f"Order failed 🛠️\n{json.dumps(err, ensure_ascii=False)}")

    # d) close all
    if low.startswith("close all"):
        try:
            res = alpaca_post("/v2/positions/close", {"cancel_orders": True})
            # res kan een lijst met gesloten posities zijn
            if isinstance(res, list) and res:
                syms = ", ".join([x.get("symbol", "?") for x in res])
                return poe_text(f"Alle posities gesloten: {syms}")
            return poe_text("Close all verzonden.")
        except requests.HTTPError as e:
            try:
                err = e.response.json()
            except Exception:
                err = {"message": str(e)}
            return poe_text(f"Close all failed 🛠️\n{json.dumps(err, ensure_ascii=False)}")

    # default: echo
    return poe_text(f"Ik ontving: '{user_text}'. Probeer: 'account', 'positions', 'buy AAPL 1', 'sell AAPL 1', of 'close all'.")
