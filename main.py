import os
import json
import requests
from fastapi import FastAPI, Request, Header, HTTPException
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

# ====== Config uit environment ======
ALPACA_KEY_ID = os.getenv("ALPACA_KEY_ID", "").strip()
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY", os.getenv("ALPACA_SECRET", "")).strip()
ALPACA_ENDPOINT = os.getenv("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets/v2").rstrip("/")
MODE = os.getenv("MODE", "alpaca_paper").strip()
POE_ACCESS_KEY = os.getenv("KEY", "").strip()  # zelfde waarde als je Poe "Access key"

# ====== Helpers Alpaca ======
def alpaca_headers():
    if not (ALPACA_KEY_ID and ALPACA_SECRET):
        raise RuntimeError("Alpaca API keys ontbreken (ALPACA_KEY_ID / ALPACA_SECRET_KEY).")
    return {
        "APCA-API-KEY-ID": ALPACA_KEY_ID,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

def get_account() -> dict:
    url = f"{ALPACA_ENDPOINT}/account"
    r = requests.get(url, headers=alpaca_headers(), timeout=15)
    r.raise_for_status()
    return r.json()

# ====== Poe Webhook model ======
class WebhookIn(BaseModel):
    text: Optional[str] = None
    message: Optional[str] = None

# ====== Poe auth check ======
def check_poe_auth(req: Request):
    # Poe kan sleutel in verschillende headers sturen; accepteer meerdere
    key = (
        req.headers.get("poe-access-key")
        or req.headers.get("x-access-key")
        or req.headers.get("x-poe-access-key")
        or req.headers.get("authorization")
    )
    if key and key.lower().startswith("bearer "):
        key = key[7:].strip()

    if POE_ACCESS_KEY:
        if not key or key != POE_ACCESS_KEY:
            raise HTTPException(status_code=403, detail="Forbidden")
    # Als POE_ACCESS_KEY leeg is, staat webhook open (niet aanbevolen).

# ====== Gezondheid / debug ======
@app.get("/")
def root():
    return {"status": "ok", "mode": MODE}

@app.get("/account")
def account_info():
    try:
        acc = get_account()
        return {
            "status": acc.get("status"),
            "currency": acc.get("currency"),
            "equity": acc.get("equity"),
            "cash": acc.get("cash"),
            "buying_power": acc.get("buying_power"),
            "portfolio_value": acc.get("portfolio_value"),
        }
    except Exception as e:
        return {"error": str(e)}

# ====== Poe webhook ======
@app.post("/webhook")
async def poe_webhook(req: Request, body: WebhookIn):
    check_poe_auth(req)
    text = (body.text or body.message or "").strip().lower()

    if text in ("help", "/help"):
        return {
            "text": (
                "🧭 *Forexboth help*\n"
                "- Typ **account** → laat Alpaca paper balans zien.\n"
                "- (Binnenkort) **buy/sell** en auto-trade.\n"
                "Je draait nu in modus: " + MODE
            )
        }

    if text == "account":
        try:
            acc = get_account()
            reply = (
                "📊 *Alpaca Paper account*\n"
                f"- Status: **{acc.get('status')}**\n"
                f"- Equity: **{acc.get('equity')}**\n"
                f"- Cash: **{acc.get('cash')}**\n"
                f"- Buying power: **{acc.get('buying_power')}**"
            )
            return {"text": reply}
        except Exception as e:
            return {"text": f"⚠️ Fout bij ophalen account: {e}"}

    # default antwoord
    return {"text": "Stuur **account** voor je balans of **help** voor uitleg."}
