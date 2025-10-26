# main.py
from fastapi import FastAPI, Header, Request, HTTPException
import os, logging
from typing import Optional
import uvicorn

app = FastAPI()

ACCESS_KEY = (os.getenv("KEY") or "").strip()

@app.get("/")
def health():
    return {"status": "ok", "mode": os.getenv("MODE", "").strip()}

@app.get("/debug/headers")
async def debug_headers(request: Request):
    # handig om te zien welke headers binnenkomen (je browser zal hier
    # geen key meesturen, Poe wel)
    return dict(request.headers)

def _match_key(candidate: Optional[str]) -> bool:
    return bool(ACCESS_KEY) and bool(candidate) and candidate.strip() == ACCESS_KEY

@app.post("/webhook")
async def webhook(
    request: Request,
    poe_access_key: Optional[str] = Header(default=None, convert_underscores=False),         # "poe-access-key"
    x_poe_access_key: Optional[str] = Header(default=None, alias="x-poe-access-key"),        # "x-poe-access-key"
    x_access_key: Optional[str] = Header(default=None, alias="x-access-key"),                # "x-access-key"
    authorization: Optional[str] = Header(default=None)                                      # "Authorization: Bearer <key>"
):
    # Pak de meegegeven key uit de headers
    bearer = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization.split(None, 1)[1].strip()

    received = poe_access_key or x_poe_access_key or x_access_key or bearer

    if not _match_key(received):
        logging.warning(f"403: verkeerde of ontbrekende key. Ontvangen={received!r}")
        raise HTTPException(status_code=403, detail="Forbidden: bad or missing access key")

    payload = await request.json()

    # === Heel simpele router ===
    text = ""
    try:
        text = (payload.get("message", {}) or {}).get("text", "")
    except Exception:
        pass

    # Demo-actie: "account" → haal Alpaca account op als MODE=alpaca_paper
    if text.strip().lower() in {"account", "acct", "status"}:
        mode = (os.getenv("MODE") or "").strip()
        if mode == "alpaca_paper":
            import requests, json, os
            alp_base = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
            key = os.getenv("ALPACA_API_KEY_ID")
            sec = os.getenv("ALPACA_API_SECRET_KEY")
            r = requests.get(
                f"{alp_base}/v2/account",
                headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec},
                timeout=15
            )
            d = r.json()
            txt = (
                f"📊 Alpaca Paper account\n"
                f"• Status: {d.get('status')}\n"
                f"• Equity: {d.get('equity')}\n"
                f"• Cash: {d.get('cash')}\n"
                f"• Buying power: {d.get('buying_power')}"
            )
        else:
            txt = f"✅ Webhook OK. MODE={mode}"
        return {"type": "text", "text": txt}

    # Default echo
    return {"type": "text", "text": "Webhook OK. Stuur 'account' om account-info op te vragen."}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
