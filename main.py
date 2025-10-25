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
