import os
import httpx
import fastapi_poe as fp
from fastapi import FastAPI
from typing import AsyncIterable

ALPACA_ENDPOINT = os.getenv("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets")
ALPACA_KEY_ID = os.getenv("ALPACA_KEY_ID", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
POE_ACCESS_KEY = os.getenv("KEY", "")  # ← dit is je Poe “Access key” (niet Alpaca)

# --- Een heel eenvoudige bot die 'account' afhandelt ---
class ForexBoth(fp.PoeBot):
    async def get_response(self, request: fp.QueryRequest) -> AsyncIterable[fp.PartialResponse]:
        user_text = (request.query[-1].content or "").strip().lower()

        if user_text in ("account", "1. account", "1 account", "1 account info", "account info"):
            # Haal accountgegevens bij Alpaca Paper
            try:
                headers = {
                    "APCA-API-KEY-ID": ALPACA_KEY_ID,
                    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
                }
                url = f"{ALPACA_ENDPOINT}/v2/account"
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.get(url, headers=headers)
                if r.status_code == 200:
                    acc = r.json()
                    equity = acc.get("equity")
                    cash = acc.get("cash")
                    buying_power = acc.get("buying_power")
                    status = acc.get("status")
                    txt = (
                        "📊 **Alpaca Paper account**\n"
                        f"• Status: {status}\n"
                        f"• Equity: {equity}\n"
                        f"• Cash: {cash}\n"
                        f"• Buying power: {buying_power}\n"
                    )
                    yield fp.PartialResponse(text=txt)
                else:
                    yield fp.PartialResponse(
                        text=f"⚠️ Alpaca error {r.status_code}: {r.text[:300]}"
                    )
            except Exception as e:
                yield fp.PartialResponse(text=f"⚠️ Fout bij Alpaca call: {e}")
            return

        # Default hulp
        help_text = (
            "Hallo! Stuur **account** om je Alpaca Paper account samen te vatten.\n"
            "Straks voegen we commando’s toe zoals *buy EURUSD*, *close all*, etc."
        )
        yield fp.PartialResponse(text=help_text)

# Maak de FastAPI-app volgens Poe’s protocol, op /webhook
app: FastAPI = fp.make_app(
    ForexBoth(path="/webhook", access_key=POE_ACCESS_KEY)
)

# Simpele health-check op /
@app.get("/")
def health():
    mode = "alpaca_paper"
    return {"status": "ok", "mode": mode}
