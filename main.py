from fastapi import FastAPI, Request
import os
from broker_alpaca import account, positions, market_order

app = FastAPI()

ACCESS_KEY = (os.getenv("ACCESS_KEY") or "").strip()
MODE = os.getenv("MODE", "alpaca_paper").strip()

def is_authorized(req: Request) -> bool:
    if not ACCESS_KEY:
        return False
    h = req.headers
    # Poe kan de key sturen als:
    # - Authorization: Bearer <key>
    # - Poe-Access-Key: <key>
    # - X-Access-Key: <key>  (fallback)
    auth = (h.get("authorization") or "").replace("Bearer ", "").strip()
    poe_key = (h.get("poe-access-key") or "").strip()
    x_key = (h.get("x-access-key") or "").strip()
    provided = auth or poe_key or x_key
    return provided == ACCESS_KEY

@app.get("/healthz")
def health():
    return {"status": "ok", "mode": MODE}

# >>> Tijdelijke debug endpoint om headers te zien (geen secrets loggen)
@app.get("/debug")
async def debug(request: Request):
    h = dict(request.headers)
    peek = {k: h.get(k) for k in ["authorization","poe-access-key","x-access-key","user-agent"]}
    return {"has_ACCESS_KEY": bool(ACCESS_KEY), "received_keys": peek}

@app.post("/webhook")
async def webhook(request: Request):
    if not is_authorized(request):
        return {"text": "Unauthorized (check ACCESS_KEY ↔ Poe Access key)"}

    data = await request.json()
    text = (data.get("message") or data.get("text") or "").strip()
    if not text:
        return {"text": "Commands: account, pos, buy <symbol> <qty>, sell <symbol> <qty>, help"}

    parts = text.split()
    cmd = parts[0].lower()

    try:
        if cmd == "help":
            return {"text": "Commands:\n- account\n- pos\n- buy <symbol> <qty>\n- sell <symbol> <qty>\nBijv: buy AAPL 1"}

        if cmd == "account":
            a = account()
            return {"text": f"Equity: ${a.get('equity')} | Cash: ${a.get('cash')} | Buying power: ${a.get('buying_power')}"}

        if cmd == "pos":
            p = positions()
            if not p:
                return {"text": "Geen open posities."}
            rows = [f"{x.get('symbol')} {x.get('qty')} @ {x.get('avg_entry_price')}" for x in p]
            return {"text": "Open posities:\n" + "\n".join(rows)}

        if cmd in ("buy", "sell"):
            if len(parts) < 3:
                return {"text": "Gebruik: buy <symbol> <qty>  (bijv. buy AAPL 1)"}
            symbol = parts[1].upper()
            qty_str = parts[2]
            try:
                qty_int = int(float(qty_str))
            except:
                qty_int = int(qty_str)
            side = "buy" if cmd == "buy" else "sell"
            o = market_order(symbol=symbol, qty=qty_int, side=side)
            return {"text": f"{side.upper()} {symbol} qty={qty_int} | status={o.get('status')} | id={o.get('id')}"}

        return {"text": "Onbekend commando. Typ 'help'."}

    except Exception as e:
        return {"text": f"Fout: {e}"}
