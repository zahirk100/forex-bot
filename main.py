from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    text = data.get("text", "")
    return {"text": f"Ontvangen van Poe: {text}"}

@app.get("/healthz")
def health():
    return {"status": "ok"}
from fastapi import FastAPI, Request
import os

app = FastAPI()

ACCESS_KEY = os.getenv("ACCESS_KEY") or "WKDJ1u0hGNA7UV95IVI0fLIPRQSUWebF"  # Zet hier je Poe key

@app.get("/healthz")
def health_check():
    return {"status": "ok"}

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    # Controleer of de juiste access key is meegegeven
    if request.headers.get("Authorization") != f"Bearer {ACCESS_KEY}":
        return {"error": "Unauthorized"}

    print("Ontvangen van Poe:", data)  # Handig voor debuggen in Render logs

    # Simpele test-reactie
    user_message = data.get("message", "Geen bericht ontvangen")
    reply = f"Je zei: {user_message}"

    return {"text": reply}
