import os, requests

BASE = os.getenv("ALPACA_API_BASE_URL", "https://paper-api.alpaca.markets/v2")
KEY  = os.getenv("ALPACA_KEY_ID")
SEC  = os.getenv("ALPACA_SECRET_KEY")
HEAD = {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SEC}

def account():
    r = requests.get(f"{BASE}/account", headers=HEAD, timeout=20)
    r.raise_for_status()
    return r.json()

def positions():
    r = requests.get(f"{BASE}/positions", headers=HEAD, timeout=20)
    r.raise_for_status()
    return r.json()

def market_order(symbol="AAPL", qty=1, side="buy", tif="gtc"):
    data = {"symbol": symbol, "qty": qty, "side": side, "type": "market", "time_in_force": tif}
    r = requests.post(f"{BASE}/orders", json=data, headers=HEAD, timeout=20)
    r.raise_for_status()
    return r.json()
