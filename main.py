import os, time
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Request, Header, HTTPException
from pydantic import BaseModel
import uvicorn
import requests

app = FastAPI()

ACCESS_KEY = os.environ.get("ACCESS_KEY", "").strip()

# ---------- Poe response helpers ----------
def poe_text(text: str, suggestions: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Poe Server Bot API minimal valid response.
    """
    resp = {
        "version": "1.0",
        "content": [{"type": "text", "text": text}],
    }
    if suggestions:
        resp["suggested_replies"] = [{"type": "text", "text": s} for s in suggestions]
    return resp

# ---------- Health ----------
@app.get("/")
def health():
    return {"status": "ok"}

# ---------- Webhook ----------
@app.post("/webhook")
async def webhook(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    poe_access_key: Optional[str] = Header(default=None, alias="poe-access-key"),
    x_access_key: Optional[str] = Header(default=None, alias="x-access-key"),
):
    # --- Access key check (any of the 3 header names accepted) ---
    incoming = poe_access_key or x_access_key or authorization
    if ACCESS_KEY:
        if not incoming or incoming.strip() != ACCESS_KEY:
            # Geef Poe duidelijke fout terug zodat je in logs kunt zien wat er mis is
            raise HTTPException(status_code=403, detail="Forbidden: bad access key")

    # --- Lees payload veilig ---
    try:
        payload = await request.json()
    except Exception:
        return poe_text("Kon de JSON van deze aanvraag niet lezen. Probeer het opnieuw.")

    # Poe stuurt meestal iets als: {"message": {"content":[{"type":"text","text":"..."}]}}
    user_text = ""
    try:
        blocks = payload.get("message", {}).get("content", [])
        for b in blocks:
            if b.get("type") == "text":
                user_text += b.get("text", "")
    except Exception:
        pass

    cmd = user_text.strip().lower()

    # --- Commands ---
    if cmd in ("help", "menu", ""):
        return poe_text(
            "Ik ben online ✅\n\nBeschikbare commando’s:\n"
            "• account – toont je Alpaca Paper-account\n"
            "• ping – snelle test\n"
            "• mode – laat huidige modus zien\n",
            suggestions=["account", "mode", "ping"]
        )

    if cmd == "ping":
        return poe_text("pong 🏓")

    if cmd == "mode":
        return poe_text(f"Servermodus: {os.environ.get('MODE','(niet gezet)')}")

    if cmd == "account":
        # Laat accountinformatie via Alpaca Paper zien (als keys aanwezig)
        ALPACA_KEY = os.environ.get("ALPACA_KEY_ID", "").strip()
        ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "").strip()
        if not (ALPACA_KEY and ALPACA_SECRET):
            return poe_text("Alpaca keys ontbreken. Zet ALPACA_KEY_ID en ALPACA_SECRET_KEY in Render → Environment.")

        endpoint = os.environ.get("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets/v2")
        try:
            r = requests.get(
                f"{endpoint}/account",
                headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
                timeout=10,
            )
            if r.status_code != 200:
                return poe_text(f"Alpaca fout ({r.status_code}): {r.text[:200]}")
            acc = r.json()
            msg = (
                "📊 Alpaca Paper account\n"
                f"• Status: {acc.get('status','?')}\n"
                f"• Equity: {acc.get('equity','?')}\n"
                f"• Cash: {acc.get('cash','?')}\n"
                f"• Buying power: {acc.get('buying_power','?')}\n"
            )
            return poe_text(msg, suggestions=["mode", "ping"])
        except Exception as e:
            return poe_text(f"Kon Alpaca niet bereiken: {e}")

    # Default: echo / niet herkend
    return poe_text(f"Ik heb “{user_text}” ontvangen, maar herken dit commando niet. Typ ‘help’.", suggestions=["help","account","mode"])
    

if __name__ == "__main__":
    # Voor lokale test (Render gebruikt je start command)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
