import os
import json
import logging
from typing import Dict, Any, Optional

import requests
from fastapi import FastAPI, Request, Header
from fastapi.responses import JSONResponse

app = FastAPI()

# === Config uit env ===
POE_KEY = os.getenv("KEY") or os.getenv("POE_ACCESS_KEY") or ""
MODE = (os.getenv("MODE") or "alpaca_paper").strip()
ALPACA_API_KEY = (os.getenv("ALPACA_API_KEY") or "").strip()
ALPACA_SECRET_KEY = (os.getenv("ALPACA_SECRET_KEY") or "").strip()

# Alpaca endpoints
ALPACA_ACCOUNT_URL = "https://paper-api.alpaca.markets/v2/account"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("poe-bot")


def poe_reply(text: str) -> Dict[str, Any]:
    """
    Return exact schema Poe expects.
    """
    return {
        "choices": [
            {
                "content": {
                    "type": "text",
                    "text": text
                },
                "is_final": True
            }
        ]
    }


@app.get("/")
def root():
    return {"status": "ok", "mode": MODE}


@app.get("/health")
def health():
    ok = bool(MODE)
    return {"ok": ok, "mode": MODE}


@app.get("/mode")
def get_mode():
    return {"mode": MODE}


def get_user_text(payload: Dict[str, Any]) -> str:
    """
    Poe server-bot payloads hebben meestal messages[-1].content[0].text
    maar we supporten ook simpele {"text":"..."} JSON om lokaal te testen.
    """
    # simpele test payload
    if "text" in payload and isinstance(payload["text"], str):
        return payload["text"]

    # Poe formaat: {"messages":[{"role":"user","content":[{"type":"text","text":"..."}]}]}
    try:
        msgs = payload.get("messages") or []
        if msgs:
            last = msgs[-1]
            content = last.get("content") or []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    return str(part.get("text") or "").strip()
    except Exception:
        pass

    return ""


def alpaca_account_text() -> str:
    if not (ALPACA_API_KEY and ALPACA_SECRET_KEY):
        return "⚠️ ALPACA_API_KEY/ALPACA_SECRET_KEY ontbreken in de Render environment."

    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }
    try:
        r = requests.get(ALPACA_ACCOUNT_URL, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            status = data.get("status")
            equity = data.get("equity")
            cash = data.get("cash")
            buying_power = data.get("buying_power")
            return (
                "📊 Alpaca Paper account\n"
                f"• Status: {status}\n"
                f"• Equity: {equity}\n"
                f"• Cash: {cash}\n"
                f"• Buying power: {buying_power}"
            )
        else:
            return f"⚠️ Alpaca account call faalde: HTTP {r.status_code} – {r.text[:200]}"
    except Exception as e:
        return f"⚠️ Fout bij Alpaca call: {e}"


@app.post("/webhook")
async def webhook(
    request: Request,
    poe_access_key: Optional[str] = Header(None, convert_underscores=False),
    authorization: Optional[str] = Header(None),
    x_poe_access_key: Optional[str] = Header(None, convert_underscores=False),
):
    """
    Poe server-bot endpoint.
    - Controleert access key in headers: 'poe-access-key' (voorkeur), of 'x-poe-access-key'.
    - Parse't het user bericht.
    - Stuurt antwoord in exact Poe-formaat.
    """
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except Exception:
        payload = {}

    # === KEY check ===
    supplied = poe_access_key or x_poe_access_key or (authorization or "").replace("Bearer ", "").strip()
    if POE_KEY:
        if not supplied or supplied.strip() != POE_KEY.strip():
            # Log veilig (niet de echte KEY printen)
            log.warning("Forbidden: access key mismatch. Received headers: poe-access-key=%s x-poe-access-key=%s auth=%s",
                        bool(poe_access_key), bool(x_poe_access_key), bool(authorization))
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})

    # === User text ===
    user_text = get_user_text(payload).lower().strip()

    # === Commands ===
    if user_text in ("help", "h", "?"):
        return poe_reply(
            "Beschikbare commando’s:\n"
            "• account – status van je Alpaca paper account\n"
            "• help – dit scherm\n"
            "\nProtip: Als je ‘account’ krijgt met fout, check in Render of ALPACA_API_KEY/ALPACA_SECRET_KEY goed staan."
        )

    if user_text.startswith("account"):
        return poe_reply(alpaca_account_text())

    if not user_text:
        return poe_reply("Ik heb geen tekst ontvangen. Typ ‘help’ of ‘account’.")
    else:
        return poe_reply(f"Ik heb je bericht ontvangen: “{user_text}”. Typ ‘help’ voor opties.")
