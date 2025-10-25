from fastapi import FastAPI, Request
import os
from broker_alpaca import account, positions, market_order

app = FastAPI()

ACCESS_KEY = os.getenv("ACCESS_KEY", "")  # Poe zet deze in de Authorization header
MODE = os.getenv("MODE", "alpaca_paper")

def unauthorized(req: Request):
    return req.headers.get("Authorization") != f"Bearer {ACCESS_KEY}"

@app.get("/healthz")
def health():
    return {"status": "ok", "mode": MODE}

@app.post("/webhook")
async def webhook(request: Request):
    if unauthorized(request):
        return {"error": "Unauthorized"}

    data = await request.json()
    text = (data.get("message") or data.get("text") or "").strip()
    if not text:
        return {"text": "Commands: account, pos, buy <symbol> <qty>, sell <symbol> <qty>, help"}

    parts = text.split()
    cmd = parts[0].lower()

    try:
        if cmd == "help":
            return {"text": "Commands:\n- account\n- pos\n- buy <symbol> <qty>\n- sell <symbol> <qty>\nVoorbeeld: buy AAPL 1  |  buy BTCUSD 0.01 (als crypto actief is)"}

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
            qty = parts[2]
            # qty moet int zijn voor stocks, voor crypto pakt Alpaca ook string; we casten safe:
            try:
                qty_int = int(float(qty))
            except:
                qty_int = int(qty)
            side = "buy" if cmd == "buy" else "sell"
            o = market_order(symbol=symbol, qty=qty_int, side=side)
            oid = o.get("id")
            status = o.get("status")
            filled = o.get("filled_qty")
            return {"text": f"{side.upper()} order: {symbol} qty={qty_int} | status={status} | filled={filled} | id={oid}"}

        return {"text": "Onbekend commando. Typ 'help'."}

    except Exception as e:
        return {"text": f"Fout: {e}"}
