from fastapi import FastAPI, Request, Header
from fastapi.responses import JSONResponse

app = FastAPI()

ACCESS_KEY = None  # vullen via env in Render

@app.on_event("startup")
async def _startup():
    import os
    global ACCESS_KEY
    ACCESS_KEY = os.getenv("ACCESS_KEY", "").strip()

@app.get("/")
def health():
    return {"status": "ok"}

@app.post("/webhook")
async def webhook(
    request: Request,
    poe_access_key: str | None = Header(default=None, alias="Poe-Access-Key"),
    x_access_key: str | None = Header(default=None, alias="X-Access-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    # 1) Key check (één van de headers moet gelijk zijn aan ACCESS_KEY)
    ok_key = False
    for k in (poe_access_key, x_access_key, authorization):
        if k and ACCESS_KEY and k.strip().replace("Bearer ", "") == ACCESS_KEY:
            ok_key = True
            break
    if not ok_key:
        # Poe verwacht bij auth-fout óók gewoon JSON terug
        return JSONResponse({"error": "forbidden"}, status_code=403)

    # 2) Poe payload lezen (maar tolerant zijn)
    try:
        body = await request.json()
    except Exception:
        body = {}

    # Tekst van de laatste user-message eruit vissen (veilig)
    user_text = ""
    try:
        msgs = body.get("messages", [])
        for m in reversed(msgs):
            if m.get("role") == "user":
                parts = m.get("content", [])
                for p in parts:
                    if p.get("type") == "text":
                        user_text = p.get("text", "")
                        raise StopIteration
    except StopIteration:
        pass

    # 3) Antwoord **in Poe-formaat**: een JSON met minimaal `text`
    reply = {
        "text": f"Ik heb je bericht ontvangen: {user_text or '(leeg)'}"
    }
    # Optioneel voorbeeld van suggesties:
    reply["suggested_replies"] = ["account", "help", "ping"]

    return JSONResponse(reply, media_type="application/json")
