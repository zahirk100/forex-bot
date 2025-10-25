# main.py
import os
import json
from typing import Any, Dict, List, Union

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

# ====== ENV ======
ALPACA_KEY_ID = os.getenv("ALPACA_KEY_ID", "").strip()
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "").strip()
ALPACA_ENDPOINT = (os.getenv("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets/v2")).rstrip("/")
MODE = os.getenv("MODE", "alpaca_paper").strip()
# Leeg = geen authorisatiecheck (handig voor debuggen)
ACCESS_KEY = (os.getenv("ACCESS_KEY") or "").strip()


# ====== HULP: AUTH ======
def _auth_ok(request: Request) -> bool:
    """
    Als ACCESS_KEY leeg is -> geen check.
    Als ACCESS_KEY is gezet, accepteer: poe-access-key, x-access-key of Authorization: Bearer ...
    """
    if not ACCESS_KEY:
        return True
    headers = request.headers
    candidate = (
        headers.get("poe-access-key")
        or headers.get("x-access-key")
        or (headers.get("authorization") or "").replace("Bearer ", "").strip()
    )
    return candidate == ACCESS_KEY


# ====== HULP: POE PAYLOAD PARSEN ======
def _extract_text(payload: Dict[str, Any]) -> str:
    """
    Probeer veilige extractie van de laatste user-tekst uit Poe-achtige payloads.
    Poe stuurt meestal: {"messages":[{"role":"user","content":"..."} , ...]}
    """
    try:
        msgs: Union[List, Dict] = payload.get("messages") or payload.get("message") or []
        if isinstance(msgs, list) and msgs:
            last = msgs[-1] or {}
            return (last.get("content") or last.get("text") or "").strip()
        if isinstance(msgs, dict):
            return (msgs.get("content") or msgs.get("text") or "").strip()
    except Exception:
        pass
    # fallback: probeer body direct
    return (payload.get("content") or payload.get("text") or "").strip()


# ====== ROOT/DEBUG ======
@app.get("/")
def root():
    return {"status": "ok", "mode": MODE}


@app.get("/headers")
async def headers(request: Request):
    """Handig voor debuggen of Poe-headers doorkomen."""
    return {
        "has_ACCESS_KEY": bool(ACCESS_KEY),
        "received_keys": {
            "authorization": request.headers.get("authorization"),
            "poe-access-key": request.headers.get("poe-access-key"),
            "x-access-key": request.headers.get("x-access-key"),
            "user-agent": request.headers.get("user-agent"),
        },
    }


# ====== BUSINESS: ALPACA ACCOUNT ======
def fetch_alpaca_account() -> str:
    """Haal account-info op bij Alpaca en geef nette tekst terug."""
    if not (ALPACA_KEY_ID and ALPACA_SECRET_KEY):
        return "Alpaca API keys ontbreken. Zet ALPACA_KEY_ID en ALPACA_SECRET_KEY in Render → Environment."

    try:
        r = requests.get(
            f"{ALPACA_ENDPOINT}/account",
            headers={
                "APCA-API-KEY-ID": ALPACA_KEY_ID,
                "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
            },
            timeout=15,
        )
    except Exception as e:
        return f"Verbindingsfout naar Alpaca: {e}"

    if r.status_code != 200:
        truncated = r.text[:300].replace("\n", " ")
        return f"Alpaca error {r.status_code}: {truncated}"

    try:
        acc = r.json()
    except Exception:
        return f"Kon Alpaca-response niet parsen: {r.text[:300]}"

    kind = "paper" if "paper" in ALPACA_ENDPOINT else "live"
    return (
        f"Alpaca ({kind})\n"
        f"Status: {acc.get('status')}\n"
        f"Equity: {acc.get('equity')}\n"
        f"Cash: {acc.get('cash')}\n"
        f"Buying power: {acc.get('buying_power')}"
    )


# ====== POE WEBHOOK ======
@app.post("/webhook")
async def webhook(request: Request):
    # Auth (alleen als ACCESS_KEY gezet is)
    if not _auth_ok(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    # Parse body veilig
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    text = _extract_text(payload).lower()

    # simpele router
    if text in ("account", "1. account", "acc"):
        reply = fetch_alpaca_account()
        return {"type": "text", "text": reply}

    # help / fallback
    help_text = (
        "Hi! Ik ben online.\n"
        "Stuur **account** om je Alpaca paper-accountstatus te zien."
    )
    return {"type": "text", "text": help_text}


# ====== LOCAL DEV (Render gebruikt z'n eigen start command) ======
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "10000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
