import os
import hmac
import time
import json
import math
import asyncio
import datetime as dt
from typing import List, Dict, Any, Optional

import requests
import numpy as np
import pandas as pd
from fastapi import FastAPI, Request, Header
from fastapi.responses import JSONResponse, PlainTextResponse

# =========================
# Config & helpers
# =========================

APP = FastAPI(title="Poe Trading Bot")

def env_bool(name: str, default: bool=False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1","true","yes","y","on"}

def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except:
        return default

def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except:
        return default

def now_utc() -> dt.datetime:
    return dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)

def _consteq(a: Optional[str], b: Optional[str]) -> bool:
    if a is None or b is None: return False
    return hmac.compare_digest(a.strip(), b.strip())

def _get_access_key_from_headers(
    authorization: str | None = None,
    poe_access_key: str | None = None,
    x_poe_access_key: str | None = None,
    x_access_key: str | None = None,
) -> Optional[str]:
    # Poe gebruikt meestal Poe-Access-Key; we accepteren meerdere opties
    if poe_access_key: return poe_access_key
    if x_poe_access_key: return x_poe_access_key
    if x_access_key: return x_access_key
    if authorization and authorization.startswith("Bearer "):
        return authorization.split("Bearer ", 1)[1]
    return None

# =========================
# Broker Abstraction
# =========================

class BrokerBase:
    name = "base"
    def account(self) -> Dict[str, Any]: raise NotImplementedError
    def positions(self) -> List[Dict[str, Any]]: raise NotImplementedError
    def instrument_ok(self, symbol: str) -> bool: raise NotImplementedError
    def fetch_candles(self, symbol: str, tf: str, limit: int=200) -> pd.DataFrame: raise NotImplementedError
    def market_order(self, symbol: str, side: str, qty: float) -> Dict[str, Any]: raise NotImplementedError
    def close_all(self, symbol: Optional[str]=None) -> Dict[str, Any]: raise NotImplementedError

# ---------- Alpaca (crypto only, e.g. BTC/USD) ----------

class AlpacaBroker(BrokerBase):
    name = "alpaca"
    def __init__(self):
        self.key = os.getenv("ALPACA_KEY_ID","")
        self.secret = os.getenv("ALPACA_SECRET_KEY","")
        self.base = os.getenv("ALPACA_BASE_URL","https://paper-api.alpaca.markets")
        self.data = os.getenv("ALPACA_DATA_URL","https://data.alpaca.markets")
        self.session = requests.Session()
        self.session.headers.update({
            "APCA-API-KEY-ID": self.key,
            "APCA-API-SECRET-KEY": self.secret
        })

    def account(self):
        r = self.session.get(f"{self.base}/v2/account", timeout=20)
        r.raise_for_status()
        return r.json()

    def positions(self):
        r = self.session.get(f"{self.base}/v2/positions", timeout=20)
        r.raise_for_status()
        return r.json()

    def instrument_ok(self, symbol: str) -> bool:
        # Alpaca crypto format: "BTC/USD"
        return "/" in symbol and symbol.split("/")[0].upper() in {"BTC","ETH","SOL","LTC","BCH","DOGE"}

    def fetch_candles(self, symbol: str, tf: str, limit: int=200) -> pd.DataFrame:
        # Use crypto bars v1beta3
        base = symbol.replace("/","")
        # map timeframe (1Min,5Min,15Min,1H,1D)
        gran = {
            "1Min": "1Min", "5Min":"5Min", "15Min":"15Min",
            "1H":"1H", "1D":"1D"
        }.get(tf, "1Min")
        url = f"{self.data}/v1beta3/crypto/us/bars"
        params = {"symbols": base, "timeframe": gran, "limit": limit}
        r = self.session.get(url, params=params, timeout=20)
        r.raise_for_status()
        js = r.json()
        bars = js.get("bars",{}).get(base,[])
        if not bars: 
            return pd.DataFrame()
        df = pd.DataFrame(bars)
        df["t"] = pd.to_datetime(df["t"], utc=True)
        df = df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume", "t":"time"})
        return df[["time","open","high","low","close","volume"]].copy()

    def market_order(self, symbol: str, side: str, qty: float):
        base = symbol.replace("/","")
        data = {
            "symbol": base,
            "side": side.lower(),
            "type": "market",
            "time_in_force": "gtc",
            "qty": str(qty)
        }
        r = self.session.post(f"{self.base}/v2/orders", json=data, timeout=20)
        if r.status_code >= 400:
            return {"error": True, "status": r.status_code, "body": r.text}
        return r.json()

    def close_all(self, symbol: Optional[str]=None):
        if symbol:
            base = symbol.replace("/","")
            r = self.session.delete(f"{self.base}/v2/positions/{base}", timeout=20)
            return {"status": r.status_code, "body": r.text}
        r = self.session.delete(f"{self.base}/v2/positions", timeout=20)
        return {"status": r.status_code, "body": r.text}

# ---------- OANDA (FX & Metals, e.g. EUR_USD, XAU_USD) ----------

class OandaBroker(BrokerBase):
    name = "oanda"
    def __init__(self):
        self.account_id = os.getenv("OANDA_ACCOUNT_ID","")
        self.token = os.getenv("OANDA_API_TOKEN","")
        # practice: https://api-fxpractice.oanda.com ; live: https://api-fxtrade.oanda.com
        self.base = os.getenv("OANDA_BASE_URL","https://api-fxpractice.oanda.com")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        })

    def account(self):
        r = self.session.get(f"{self.base}/v3/accounts/{self.account_id}", timeout=20)
        r.raise_for_status()
        acc = r.json().get("account",{})
        return {
            "id": acc.get("id"),
            "currency": acc.get("currency"),
            "balance": float(acc.get("balance","0")),
            "openTradeCount": int(acc.get("openTradeCount","0")),
            "marginAvailable": float(acc.get("marginAvailable","0")),
        }

    def positions(self):
        r = self.session.get(f"{self.base}/v3/accounts/{self.account_id}/openPositions", timeout=20)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        out = []
        for p in r.json().get("positions",[]):
            out.append({
                "instrument": p.get("instrument"),
                "longUnits": p.get("long",{}).get("units"),
                "shortUnits": p.get("short",{}).get("units"),
            })
        return out

    def instrument_ok(self, symbol: str) -> bool:
        # OANDA formaat: EUR_USD, XAU_USD, GBP_USD, etc.
        return "_" in symbol

    def fetch_candles(self, symbol: str, tf: str, limit: int=200) -> pd.DataFrame:
        gran = {
            "1Min": "M1", "5Min":"M5", "15Min":"M15",
            "1H":"H1", "1D":"D"
        }.get(tf, "M1")
        url = f"{self.base}/v3/instruments/{symbol}/candles"
        params = {"count": limit, "price": "M", "granularity": gran}
        r = self.session.get(url, params=params, timeout=20)
        r.raise_for_status()
        candles = r.json().get("candles",[])
        rows = []
        for c in candles:
            mid = c.get("mid",{})
            rows.append({
                "time": pd.to_datetime(c.get("time"), utc=True),
                "open": float(mid.get("o","0")),
                "high": float(mid.get("h","0")),
                "low": float(mid.get("l","0")),
                "close": float(mid.get("c","0")),
                "volume": c.get("volume",0)
            })
        df = pd.DataFrame(rows)
        return df

    def market_order(self, symbol: str, side: str, qty: float):
        units = str(int(qty if side.lower()=="buy" else -qty))
        data = {"order": {
            "units": units,
            "instrument": symbol,
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }}
        r = self.session.post(f"{self.base}/v3/accounts/{self.account_id}/orders", json=data, timeout=20)
        if r.status_code >= 400:
            return {"error": True, "status": r.status_code, "body": r.text}
        return r.json()

    def close_all(self, symbol: Optional[str]=None):
        if symbol:
            # Close both long & short legs
            r = self.session.put(f"{self.base}/v3/accounts/{self.account_id}/positions/{symbol}/close", json={"longUnits":"ALL","shortUnits":"ALL"}, timeout=20)
            return {"status": r.status_code, "body": r.text}
        # OANDA heeft geen "close all" endpoint; je zou alle open positions moeten ophalen en per stuk sluiten.
        pos = self.positions()
        out = []
        for p in pos:
            sym = p["instrument"]
            r = self.session.put(f"{self.base}/v3/accounts/{self.account_id}/positions/{sym}/close", json={"longUnits":"ALL","shortUnits":"ALL"}, timeout=20)
            out.append({sym: r.status_code})
        return {"positions_closed": out}

# =========================
# Strategy (scalp-ish)
# =========================

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """EMA(9/21) cross + RSI(14) filter + ATR(14)."""
    if df.empty:
        return df
    x = df.copy()
    x["ema_fast"] = x["close"].ewm(span=9, adjust=False).mean()
    x["ema_slow"] = x["close"].ewm(span=21, adjust=False).mean()

    # RSI
    delta = x["close"].diff()
    gain = (delta.clip(lower=0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / (loss.replace(0, np.nan))
    x["rsi"] = 100 - (100 / (1 + rs))

    # ATR
    tr = np.maximum(x["high"]-x["low"], np.maximum(abs(x["high"]-x["close"].shift()), abs(x["low"]-x["close"].shift())))
    x["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()
    return x

def generate_signal(df: pd.DataFrame) -> str:
    """Return 'buy', 'sell' or ''."""
    if len(df) < 25: return ""
    last = df.iloc[-1]
    prev = df.iloc[-2]
    # Cross
    bull_cross = prev["ema_fast"] <= prev["ema_slow"] and last["ema_fast"] > last["ema_slow"]
    bear_cross = prev["ema_fast"] >= prev["ema_slow"] and last["ema_fast"] < last["ema_slow"]
    # RSI filter
    if bull_cross and last["rsi"] < 70:
        return "buy"
    if bear_cross and last["rsi"] > 30:
        return "sell"
    return ""

# =========================
# Trading Engine
# =========================

class Engine:
    def __init__(self):
        self.mode = os.getenv("MODE","alpaca_paper").strip()
        self.symbols = [s.strip() for s in os.getenv("TRADE_SYMBOLS","BTC/USD").split(",") if s.strip()]
        self.timeframe = os.getenv("TIMEFRAME","1Min")
        self.risk_percent = env_float("RISK_PERCENT", 0.5)   # % of equity per trade
        self.max_positions = env_int("MAX_POSITIONS", 3)
        self.enable_auto = env_bool("ENABLE_AUTOTRADE", True)

        # Choose brokers
        self.alpaca = AlpacaBroker()
        self.oanda = OandaBroker()
        self.bg_task = None
        self.running = False

    def _broker_for(self, symbol: str) -> Optional[BrokerBase]:
        """Route symbol -> broker."""
        if self.alpaca.instrument_ok(symbol):
            return self.alpaca
        if self.oanda.instrument_ok(symbol):
            return self.oanda
        return None

    def status(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "symbols": self.symbols,
            "timeframe": self.timeframe,
            "risk_percent": self.risk_percent,
            "max_positions": self.max_positions,
            "auto": self.enable_auto,
            "running": self.running
        }

    def _equity(self, br: BrokerBase) -> float:
        try:
            acc = br.account()
            # Alpaca has 'equity'; OANDA returns 'balance'
            return float(acc.get("equity", acc.get("balance", 0)))
        except Exception as e:
            print("equity_err", e)
            return 0.0

    def _size_from_risk(self, br: BrokerBase, symbol: str, df: pd.DataFrame) -> float:
        # position size = (equity * risk%) / (ATR)
        eq = self._equity(br)
        last = df.iloc[-1]
        atr = max(float(last["atr"]), 1e-8)
        risk_amt = eq * (self.risk_percent/100.0)
        # For Alpaca crypto qty is in units; for OANDA units is integer (contracts)
        qty = risk_amt / atr
        # clamp
        return max(1.0, round(qty, 6))

    def _trade_once(self, symbol: str) -> Dict[str, Any]:
        br = self._broker_for(symbol)
        if not br:
            return {"symbol": symbol, "error": "No broker supports this symbol"}

        df = br.fetch_candles(symbol, self.timeframe, 300)
        if df.empty:
            return {"symbol": symbol, "warn": "no candles"}
        df = compute_indicators(df)
        sig = generate_signal(df)
        if not sig:
            return {"symbol": symbol, "signal": ""}

        # Limit by open positions (simple)
        try:
            pos = br.positions()
            if isinstance(pos, list) and len(pos) >= self.max_positions:
                return {"symbol": symbol, "info": "max positions reached"}
        except Exception as e:
            print("pos_err", e)

        qty = self._size_from_risk(br, symbol, df)
        order = br.market_order(symbol, sig, qty)
        return {"symbol": symbol, "signal": sig, "qty": qty, "order": order}

    async def _loop(self):
        self.running = True
        try:
            while self.enable_auto:
                results = []
                for sym in self.symbols:
                    try:
                        r = self._trade_once(sym)
                        results.append(r)
                    except Exception as e:
                        results.append({"symbol": sym, "error": str(e)})
                print("cycle", now_utc().isoformat(), results)
                # wacht 60s (scalp-ish). Pas aan via SLEEP_SECONDS env als je wilt.
                await asyncio.sleep(env_int("SLEEP_SECONDS", 60))
        finally:
            self.running = False

    def start(self):
        if self.running:
            return {"running": True}
        self.enable_auto = True
        self.bg_task = asyncio.create_task(self._loop())
        return {"started": True}

    def stop(self):
        self.enable_auto = False
        return {"stopping": True}

ENGINE = Engine()

# =========================
# Routes
# =========================

@APP.get("/")
def root():
    return {"status":"ok", "mode": os.getenv("MODE","alpaca_paper")}

@APP.get("/inspect")
async def inspect(request: Request):
    headers = dict(request.headers)
    mask = {k: (v if "key" not in k.lower() else "***") for k,v in headers.items()}
    return mask

@APP.post("/webhook")
async def webhook(
    request: Request,
    authorization: str | None = Header(default=None),
    poe_access_key: str | None = Header(default=None, alias="Poe-Access-Key"),
    x_poe_access_key: str | None = Header(default=None, alias="X-Poe-Access-Key"),
    x_access_key: str | None = Header(default=None, alias="X-Access-Key"),
):
    expected = os.getenv("KEY","")
    received = _get_access_key_from_headers(
        authorization=authorization,
        poe_access_key=poe_access_key,
        x_poe_access_key=x_poe_access_key,
        x_access_key=x_access_key,
    )
    if not expected or not _consteq(expected, received):
        print("AUTH_FAIL", {"has_expected": bool(expected), "received_prefix": (received or "")[:3]})
        return JSONResponse({"error":"forbidden"}, status_code=403)

    body = await request.json()
    # Poe payloads: we pakken laatste user message simplistisch
    text = ""
    try:
        msgs = body.get("messages") or []
        if msgs:
            text = (msgs[-1].get("content","") or "").strip().lower()
        else:
            text = (body.get("text","") or "").strip().lower()
    except:
        text = ""

    # Simple intents
    if text in {"account","acc"}:
        # Toon per broker die relevant is voor je symbols
        out = []
        brokers = set()
        for s in ENGINE.symbols:
            b = ENGINE._broker_for(s)
            if b and b.name not in brokers:
                try:
                    out.append({b.name: b.account()})
                except Exception as e:
                    out.append({b.name: {"error": str(e)}})
                brokers.add(b.name)
        content = json.dumps(out, indent=2)
        return JSONResponse({"text": content})

    if text in {"positions","pos"}:
        out = []
        brokers = set()
        for s in ENGINE.symbols:
            b = ENGINE._broker_for(s)
            if b and b.name not in brokers:
                try:
                    out.append({b.name: b.positions()})
                except Exception as e:
                    out.append({b.name: {"error": str(e)}})
                brokers.add(b.name)
        return JSONResponse({"text": json.dumps(out, indent=2)})

    if text in {"start","run"}:
        res = ENGINE.start()
        return JSONResponse({"text": f"Auto trade gestart: {res}"})

    if text in {"stop","pause"}:
        res = ENGINE.stop()
        return JSONResponse({"text": f"Auto trade stoppen: {res}"})

    if text in {"status","config"}:
        return JSONResponse({"text": json.dumps(ENGINE.status(), indent=2)})

    if text.startswith("close"):
        # close all or one symbol
        parts = text.split()
        sym = None if len(parts)==1 else parts[1].upper()
        if sym:
            b = ENGINE._broker_for(sym)
            if not b:
                return JSONResponse({"text": f"Geen broker voor {sym}"})
            res = b.close_all(sym)
            return JSONResponse({"text": f"Close {sym}: {res}"})
        else:
            # close via alle brokers
            done = {}
            for s in set(ENGINE.symbols):
                b = ENGINE._broker_for(s)
                if b and b.name not in done:
                    done[b.name] = b.close_all(None)
            return JSONResponse({"text": f"Close all: {done}"})

    # fallback: 1 scan & evt trade
    results = []
    for s in ENGINE.symbols:
        try:
            results.append(ENGINE._trade_once(s))
        except Exception as e:
            results.append({"symbol": s, "error": str(e)})
    return JSONResponse({"text": json.dumps(results, indent=2)})

# Uvicorn entrypoint (Render start command: uvicorn main:APP --host 0.0.0.0 --port $PORT)
