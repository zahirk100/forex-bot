# main.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import os
import requests

app = FastAPI()

@app.get("/")
def root():
    # laat zien in welke modus we draaien
    return {"status": "ok", "mode": os.getenv("MODE", "unknown")}

@app.post("/webhook")
async def webhook(req: Request):
    """
    MINIMALE ANTWOORD-LOGGER:
    - leest Poe-payload
    - pakt de laatste user-boodschap (als die er is)
    - stuurt een simpele tekst terug in het juiste Poe-formaat
    """
    try:
        data = await req.json()
    except Exception:
        return JSONResponse({"type": "text", "text": "Kon JSON niet lezen."})

    # haal laatste user message eruit (als aanwezig)
    user_text = ""
    try:
        msgs = data.get("messages", [])
        for m in reversed(msgs):
            if m.get("role") == "user":
                user_text = m.get("content", "")
                break
    except Exception:
        pass

    if not user_text:
        user_text = "(geen user tekst ontvangen)"

    # simpele router: zeg iets terug zodat we zeker weten dat Poe het toont
    reply = f"Ik heb je bericht ontvangen: {user_text}"

    # *** BELANGRIJK: precies dit formaat teruggeven ***
    return JSONResponse({"type": "text", "text": reply})

# (optioneel) favicon om 404-spam in logs te vermijden
@app.get("/favicon.ico")
def favicon():
    return JSONResponse(content=None, status_code=204)
