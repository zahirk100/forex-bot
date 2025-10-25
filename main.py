import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from oandapyV20 import API
import oandapyV20.endpoints.accounts as accounts
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.positions as positions
import oandapyV20.endpoints.trades as trades

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

@dataclass
class Signal:
    direction: str  # "long", "short", "flat"
    stop_price: float
    take_profit_price: float


class OandaTradingBot:
    def __init__(self):
        self.api_key = self._get_env("OANDA_API_KEY")
        self.account_id = self._get_env("OANDA_ACCOUNT_ID")
        environment = os.getenv("OANDA_ENVIRONMENT", "practice")
        self.instrument = os.getenv("OANDA_INSTRUMENT", "EUR_USD").upper()
        self.granularity = os.getenv("GRANULARITY", "M5")
        self.ema_fast = int(os.getenv("EMA_FAST", "9"))
        self.ema_slow = int(os.getenv("EMA_SLOW", "21"))
        self.rsi_period = int(os.getenv("RSI_PERIOD", "14"))
        self.rsi_overbought = float(os.getenv("RSI_OVERBOUGHT", "70"))
        self.rsi_oversold = float(os.getenv("RSI_OVERSOLD", "30"))
        self.atr_period = int(os.getenv("ATR_PERIOD", "14"))
        self.atr_multiplier = float(os.getenv("ATR_MULTIPLIER", "1.5"))
        self.reward_risk = float(os.getenv("REWARD_RISK", "2.0"))
        self.max_units = int(os.getenv("MAX_POSITION_UNITS", "1000"))
        self.risk_per_trade_pct = float(os.getenv("RISK_PER_TRADE_PCT", "1.0")) / 100.0
        self.poll_interval = int(os.getenv("POLL_INTERVAL_SEC", "60"))
        self.enable_trading = os.getenv("ENABLE_TRADING", "false").lower() == "true"

        self.client = API(access_token=self.api_key, environment=environment)
        self.instrument_details = self._fetch_instrument_details()

        logging.info(
            "Bot initialised for %s on %s (EMA %s/%s, RSI %s, ATR x%s, RR %.2f, Risk %.2f%%)",
            self.instrument,
            self.granularity,
            self.ema_fast,
            self.ema_slow,
            self.rsi_period,
            self.atr_multiplier,
            self.reward_risk,
            self.risk_per_trade_pct * 100,
        )

    @staticmethod
    def _get_env(name: str) -> str:
        value = os.getenv(name)
        if not value:
            raise ValueError(f"Environment variable {name} is required.")
        return value

    def _fetch_instrument_details(self) -> dict:
        req = accounts.AccountInstruments(
            accountID=self.account_id,
            params={"instruments": self.instrument}
        )
        response = self.client.request(req)
        for inst in response.get("instruments", []):
            if inst["name"] == self.instrument:
                logging.info(
                    "Instrument details: displayPrecision=%s, pipLocation=%s",
                    inst["displayPrecision"],
                    inst["pipLocation"],
                )
                return inst
        raise RuntimeError(f"Instrument {self.instrument} not found for account {self.account_id}")

    def fetch_candles(self, count: int = 300) -> pd.DataFrame:
        params = {
            "granularity": self.granularity,
            "count": count,
            "price": "M",
        }
        req = instruments.InstrumentsCandles(
            instrument=self.instrument,
            params=params
        )
        candles = self.client.request(req)["candles"]
        records = []
        for candle in candles:
            if not candle["complete"]:
                continue
            mid = candle["mid"]
            records.append(
                {
                    "time": pd.Timestamp(candle["time"]),
                    "open": float(mid["o"]),
                    "high": float(mid["h"]),
                    "low": float(mid["l"]),
                    "close": float(mid["c"]),
                }
            )
        if not records:
            raise RuntimeError("No complete candles received")
        df = pd.DataFrame(records).set_index("time")
        return df

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["ema_fast"] = df["close"].ewm(span=self.ema_fast, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=self.ema_slow, adjust=False).mean()

        delta = df["close"].diff()
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        roll_up = pd.Series(gain, index=df.index).ewm(alpha=1 / self.rsi_period, adjust=False).mean()
        roll_down = pd.Series(loss, index=df.index).ewm(alpha=1 / self.rsi_period, adjust=False).mean()
        rs = roll_up / (roll_down + 1e-12)
        df["rsi"] = 100 - (100 / (1 + rs))

        df["prev_close"] = df["close"].shift(1)
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - df["prev_close"]).abs()
        tr3 = (df["low"] - df["prev_close"]).abs()
        df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["atr"] = df["tr"].rolling(window=self.atr_period).mean()
        return df

    def get_account_balance(self) -> float:
        req = accounts.AccountSummary(self.account_id)
        summary = self.client.request(req)["account"]
        balance = float(summary["balance"])
        logging.info("Account balance: %.2f", balance)
        return balance

    def get_open_position(self) -> dict | None:
        req = positions.PositionDetails(self.account_id, self.instrument)
        try:
            position = self.client.request(req)["position"]
        except Exception:
            return None
        long_units = float(position["long"]["units"])
        short_units = float(position["short"]["units"])
        if long_units != 0:
            return {"side": "long", "units": long_units}
        if short_units != 0:
            return {"side": "short", "units": short_units}
        return None

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        last = df.iloc[-1]
        prev = df.iloc[-2]

        if np.isnan([last["ema_fast"], last["ema_slow"], last["rsi"], last["atr"]]).any():
            logging.info("Indicators not ready yet.")
            return Signal("flat", 0, 0)

        spread = self.fetch_spread()
        logging.info("Current spread: %.5f", spread)

        # BUY signal
        if (
            prev["ema_fast"] <= prev["ema_slow"]
            and last["ema_fast"] > last["ema_slow"]
            and last["rsi"] < self.rsi_overbought
        ):
            stop_distance = max(last["atr"] * self.atr_multiplier, spread * 2)
            return self._build_signal("long", last["close"], stop_distance)

        # SELL signal
        if (
            prev["ema_fast"] >= prev["ema_slow"]
            and last["ema_fast"] < last["ema_slow"]
            and last["rsi"] > self.rsi_oversold
        ):
            stop_distance = max(last["atr"] * self.atr_multiplier, spread * 2)
            return self._build_signal("short", last["close"], stop_distance)

        return Signal("flat", 0, 0)

    def _build_signal(self, direction: str, price: float, stop_distance: float) -> Signal:
        precision = int(self.instrument_details["displayPrecision"])
        if direction == "long":
            stop_price = round(price - stop_distance, precision)
            tp_price = round(price + stop_distance * self.reward_risk, precision)
        else:
            stop_price = round(price + stop_distance, precision)
            tp_price = round(price - stop_distance * self.reward_risk, precision)

        logging.info(
            "Signal %s | entry≈%.5f, stop=%.5f, take-profit=%.5f",
            direction.upper(), price, stop_price, tp_price
        )
        return Signal(direction, stop_price, tp_price)

    def fetch_spread(self) -> float:
        params = {"instruments": self.instrument}
        pricing = self.client.request(
            trades.OpenTrades(self.account_id)
        )  # fallback in case open positions needed
        req = instruments.InstrumentsOrderBook(self.instrument)
        try:
            orderbook = self.client.request(req)
            mid_price = float(orderbook["price"])
            buckets = orderbook["buckets"]
            if not buckets:
                return 0.0
            highest_bid = max(float(b["price"]) for b in buckets if b["shortCountPercent"] > 0)
            lowest_ask = min(float(b["price"]) for b in buckets if b["longCountPercent"] > 0)
            return abs(lowest_ask - highest_bid)
        except Exception:
            return 0.0

    def calculate_position_size(self, stop_distance: float, balance: float) -> int:
        pip_location = int(self.instrument_details["pipLocation"])
        pip_value = 10 ** pip_location
        price_value = stop_distance

        if price_value <= 0:
            return 0

        risk_amount = balance * self.risk_per_trade_pct
        units = risk_amount / price_value
        units = min(abs(int(units)), self.max_units)
        return max(units, 0)

    def place_order(self, signal: Signal, units: int):
        if units <= 0:
            logging.warning("Units <= 0, order skipped.")
            return

        order_side_units = units if signal.direction == "long" else -units
        price_precision = int(self.instrument_details["displayPrecision"])

        order_data = {
            "order": {
                "type": "MARKET",
                "instrument": self.instrument,
                "units": str(order_side_units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                "stopLossOnFill": {"price": format(signal.stop_price, f".{price_precision}f")},
                "takeProfitOnFill": {"price": format(signal.take_profit_price, f".{price_precision}f")},
            }
        }

        if not self.enable_trading:
            logging.info("[DRY RUN] Would place order: %s", order_data)
            return

        req = orders.OrderCreate(self.account_id, data=order_data)
        response = self.client.request(req)
        logging.info("Order response: %s", response)

    def close_position(self, side: str):
        if not self.enable_trading:
            logging.info("[DRY RUN] Would close %s position.", side)
            return

        data = {"longUnits": "ALL"} if side == "long" else {"shortUnits": "ALL"}
        req = positions.PositionClose(self.account_id, self.instrument, data=data)
        response = self.client.request(req)
        logging.info("Position close response: %s", response)

    def sync_trades(self):
        open_positions = self.get_open_position()
        logging.info("Open position: %s", open_positions)

    def run(self):
        while True:
            try:
                candles_df = self.fetch_candles()
                candles_df = self.compute_indicators(candles_df)

                signal = self.generate_signal(candles_df)
                position = self.get_open_position()

                if signal.direction == "flat":
                    logging.info("No actionable signal.")
                else:
                    if position and position["side"] != signal.direction:
                        logging.info("Opposite position detected. Closing...")
                        self.close_position(position["side"])
                        position = None

                    if not position:
                        balance = self.get_account_balance()
                        last_close = candles_df["close"].iloc[-1]
                        stop_distance = abs(last_close - signal.stop_price)
                        units = self.calculate_position_size(stop_distance, balance)
                        logging.info("Calculated units: %s", units)
                        self.place_order(signal, units)
                    else:
                        logging.info("Existing position aligns with signal, nothing to do.")

            except KeyboardInterrupt:
                logging.info("Interrupted by user. Exiting.")
                break
            except Exception as exc:
                logging.exception("Error in run loop: %s", exc)

            time.sleep(self.poll_interval)


if __name__ == "__main__":
    bot = OandaTradingBot()
    bot.run()
