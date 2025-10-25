from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
import os, json, httpx

app = FastAPI()

MODE = os.getenv("MODE", "alpaca_paper").strip()
ALPACA_KEY = os.getenv("ALPACA_API_KEY", "").strip()
ALPACA_SECRET = os.getenv("ALPACA_API_SECRET", "").strip()
ALPACA_BASE = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").strip()

# --- Simple health/info endpoints ---
@app.get("/")
def root():
    return {"status": "ok", "mode": MODE}

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/inspect")
async def inspect(request: Request):
    # Handy to see what headers Poe sends (in browser they'll be null – that’s fine)
    hdrs = {k.lower(): v for k, v in request.headers.items()}
    return PlainTextResponse(json.dumps({
        "has_ACCESS_KEY": bool(os.getenv("ACCESS_KEY")),
        "received_keys": {
            "authorization": hdrs.get("authorization"),
            "poe-access-key": hdrs.get("poe-access-key"),
            "x-access-key": hdrs.get("x-access-key"),
            "user-agent": hdrs.get("user-agent")
        }
    }))

# --- Helper: Alpaca account ---
async def alpaca_account():
    if not (ALPACA_KEY and ALPACA_SECRET):
        return {"error": "alpaca_keys_missing"}
    url = f"{ALPACA_BASE}/v2/account"
    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        data = r.json()
        # Return a tiny summary
        return {
            "id": data.get("id"),
            "status": data.get("status"),
            "currency": data.get("currency"),
            "cash": data.get("cash"),
            "portfolio_value": data.get("portfolio_value"),
        }

# --- Poe webhook (POST only) ---
@app.post("/webhook")
async def webhook(request: Request):
    """
    Poe will POST JSON like:
    {"message": "account", "user_id":"...", ...}
    We DO NOT enforce access key for now to avoid 403 while debugging.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    text = str(body.get("message", "")).strip().lower()
    # Very simple router
    if text == "account":
        try:
            acc = await alpaca_account()
            if "error" in acc:
                return JSONResponse({"ok": False, "error": acc["error"]}, status_code=200)
            return JSONResponse({"ok": True, "type": "account", "data": acc}, status_code=200)
        except httpx.HTTPError as e:
            return JSONResponse({"ok": False, "error": f"alpaca_http_error: {e}"}, status_code=200)
    else:
        return JSONResponse({"ok": True, "echo": text or "(empty)"}, status_code=200)
