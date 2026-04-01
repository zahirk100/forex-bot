import json
import logging
import os
from typing import Dict, Any, Optional

import requests
from fastapi import FastAPI, Request, Header
from fastapi.responses import JSONResponse, HTMLResponse

from bot_manager import BotManager

app = FastAPI()
manager = BotManager()

POE_KEY = os.getenv("KEY") or os.getenv("POE_ACCESS_KEY") or ""
MODE = (os.getenv("MODE") or "alpaca_paper").strip()
ALPACA_API_KEY = (os.getenv("ALPACA_API_KEY") or "").strip()
ALPACA_SECRET_KEY = (os.getenv("ALPACA_SECRET_KEY") or "").strip()
ALPACA_ACCOUNT_URL = "https://paper-api.alpaca.markets/v2/account"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("poe-bot")


def poe_reply(text: str) -> Dict[str, Any]:
    return {
        "choices": [
            {
                "content": {"type": "text", "text": text},
                "is_final": True,
            }
        ]
    }


@app.get("/")
def root():
    return {"status": "ok", "mode": MODE, "mt5_bot_running": manager.status()["running"]}


@app.get("/health")
def health():
    return {"ok": True, "mode": MODE}


@app.get("/mode")
def get_mode():
    return {"mode": MODE}


@app.get("/mt5/status")
def mt5_status():
    return manager.status()


@app.post("/mt5/start")
def mt5_start():
    return manager.start()


@app.post("/mt5/stop")
def mt5_stop():
    return manager.stop()


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    return """
<!doctype html>
<html>
  <head><meta charset='utf-8'><title>MT5 Bot Dashboard</title></head>
  <body style='font-family:Arial;max-width:760px;margin:40px auto;'>
    <h2>MT5 Bot Dashboard</h2>
    <p>Start/stop en status van je bot.</p>
    <button onclick='cmd("start")'>Start</button>
    <button onclick='cmd("stop")'>Stop</button>
    <button onclick='refresh()'>Refresh</button>
    <pre id='out' style='background:#111;color:#0f0;padding:12px;border-radius:8px;'></pre>
    <script>
      async function cmd(action){
        const r = await fetch('/mt5/' + action, {method:'POST'});
        const j = await r.json();
        document.getElementById('out').textContent = JSON.stringify(j,null,2);
        await refresh();
      }
      async function refresh(){
        const r = await fetch('/mt5/status');
        const j = await r.json();
        document.getElementById('out').textContent = JSON.stringify(j,null,2);
      }
      refresh();
      setInterval(refresh, 5000);
    </script>
  </body>
</html>
"""


def get_user_text(payload: Dict[str, Any]) -> str:
    if "text" in payload and isinstance(payload["text"], str):
        return payload["text"]

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
        return "⚠️ ALPACA_API_KEY/ALPACA_SECRET_KEY ontbreken in de environment."

    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }
    try:
        r = requests.get(ALPACA_ACCOUNT_URL, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return (
                "📊 Alpaca Paper account\n"
                f"• Status: {data.get('status')}\n"
                f"• Equity: {data.get('equity')}\n"
                f"• Cash: {data.get('cash')}\n"
                f"• Buying power: {data.get('buying_power')}"
            )
        return f"⚠️ Alpaca call faalde: HTTP {r.status_code}"
    except Exception as e:
        return f"⚠️ Fout bij Alpaca call: {e}"


@app.post("/webhook")
async def webhook(
    request: Request,
    poe_access_key: Optional[str] = Header(None, convert_underscores=False),
    authorization: Optional[str] = Header(None),
    x_poe_access_key: Optional[str] = Header(None, convert_underscores=False),
):
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except Exception:
        payload = {}

    supplied = poe_access_key or x_poe_access_key or (authorization or "").replace("Bearer ", "").strip()
    if POE_KEY and (not supplied or supplied.strip() != POE_KEY.strip()):
        log.warning("Forbidden: access key mismatch")
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})

    user_text = get_user_text(payload).lower().strip()

    if user_text in ("help", "h", "?"):
        return poe_reply("Beschikbare commando’s: account, help")
    if user_text.startswith("account"):
        return poe_reply(alpaca_account_text())
    if not user_text:
        return poe_reply("Ik heb geen tekst ontvangen. Typ ‘help’.")
    return poe_reply(f"Ik heb je bericht ontvangen: {user_text}")
