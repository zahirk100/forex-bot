from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

def poe_ok(msg="Pong ✅"):
    return JSONResponse({"type": "text", "text": msg})

@app.get("/")
def root_get():
    return poe_ok("Root OK ✅")

@app.post("/")
async def root_post(_: Request):
    return poe_ok("Root Pong ✅")

@app.get("/webhook")
def webhook_get():
    return poe_ok("Webhook OK ✅")

@app.post("/webhook")
async def webhook_post(_: Request):
    return poe_ok("Webhook Pong ✅")

@app.get("/favicon.ico")
def favicon():
    return JSONResponse(status_code=204, content=None)
