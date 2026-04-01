import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import date
from threading import Event
from typing import Optional, Tuple, Dict, Any

import MetaTrader5 as mt5
import numpy as np
import pandas as pd


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mt5-forex-bot")


@dataclass
class BotConfig:
    symbol: str = os.getenv("MT5_SYMBOL", "EURUSD")
    timeframe: int = getattr(mt5, os.getenv("MT5_TIMEFRAME", "TIMEFRAME_M5"), mt5.TIMEFRAME_M5)
    trend_timeframe: int = getattr(mt5, os.getenv("MT5_TREND_TIMEFRAME", "TIMEFRAME_H1"), mt5.TIMEFRAME_H1)
    bars: int = int(os.getenv("BOT_BARS", "500"))

    ema_fast: int = int(os.getenv("BOT_EMA_FAST", "20"))
    ema_slow: int = int(os.getenv("BOT_EMA_SLOW", "50"))
    ema_trend: int = int(os.getenv("BOT_EMA_TREND", "200"))
    rsi_period: int = int(os.getenv("BOT_RSI_PERIOD", "14"))
    rsi_buy_threshold: float = float(os.getenv("BOT_RSI_BUY", "55"))
    rsi_sell_threshold: float = float(os.getenv("BOT_RSI_SELL", "45"))
    atr_period: int = int(os.getenv("BOT_ATR_PERIOD", "14"))

    risk_per_trade_pct: float = float(os.getenv("BOT_RISK_PER_TRADE_PCT", "0.5"))
    max_daily_loss_pct: float = float(os.getenv("BOT_MAX_DAILY_LOSS_PCT", "2.0"))
    atr_sl_multiple: float = float(os.getenv("BOT_ATR_SL_MULTIPLE", "1.5"))
    atr_tp_multiple: float = float(os.getenv("BOT_ATR_TP_MULTIPLE", "2.5"))
    max_spread_points: int = int(os.getenv("BOT_MAX_SPREAD_POINTS", "25"))
    max_open_positions: int = int(os.getenv("BOT_MAX_OPEN_POSITIONS", "1"))
    cooldown_seconds: int = int(os.getenv("BOT_COOLDOWN_SECONDS", "180"))

    deviation: int = int(os.getenv("BOT_DEVIATION", "20"))
    poll_seconds: int = int(os.getenv("BOT_POLL_SECONDS", "30"))
    magic: int = int(os.getenv("BOT_MAGIC", "20260401"))

    def to_public_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MT5ForexBot:
    def __init__(self, config: BotConfig):
        self.config = config
        self.day_start_equity: Optional[float] = None
        self.day_marker: Optional[date] = None
        self.last_trade_ts: float = 0.0
        self.connected = False

    def connect(self) -> None:
        if self.connected:
            return

        login = os.getenv("MT5_LOGIN")
        password = os.getenv("MT5_PASSWORD")
        server = os.getenv("MT5_SERVER")

        if login and password and server:
            ok = mt5.initialize(login=int(login), password=password, server=server)
        else:
            ok = mt5.initialize()

        if not ok:
            raise RuntimeError(f"MT5 initialize faalde: {mt5.last_error()}")

        account = mt5.account_info()
        if account is None:
            raise RuntimeError(f"Kon account info niet ophalen: {mt5.last_error()}")

        self.day_start_equity = float(account.equity)
        self.day_marker = date.today()
        self.connected = True
        log.info("Verbonden met account=%s server=%s equity=%.2f", account.login, account.server, account.equity)

    def shutdown(self) -> None:
        mt5.shutdown()
        self.connected = False

    def _roll_day_if_needed(self) -> None:
        today = date.today()
        if self.day_marker != today:
            account = mt5.account_info()
            if account is not None:
                self.day_start_equity = float(account.equity)
                self.day_marker = today
                log.info("Nieuwe handelsdag. Nieuwe baseline equity=%.2f", self.day_start_equity)

    def _daily_loss_exceeded(self) -> bool:
        self._roll_day_if_needed()
        account = mt5.account_info()
        if account is None or self.day_start_equity is None:
            return False

        change_pct = ((float(account.equity) - self.day_start_equity) / self.day_start_equity) * 100
        if change_pct <= -abs(self.config.max_daily_loss_pct):
            log.warning("Max daily loss geraakt: %.2f%% <= -%.2f%%", change_pct, self.config.max_daily_loss_pct)
            return True
        return False

    def _rates(self, timeframe: int) -> pd.DataFrame:
        rates = mt5.copy_rates_from_pos(self.config.symbol, timeframe, 0, self.config.bars)
        if rates is None or len(rates) < max(self.config.ema_trend, self.config.ema_slow, self.config.rsi_period) + 10:
            raise RuntimeError(f"Onvoldoende candles voor {self.config.symbol}/{timeframe}: {mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        up = np.where(delta > 0, delta, 0.0)
        down = np.where(delta < 0, -delta, 0.0)
        roll_up = pd.Series(up, index=close.index).ewm(alpha=1 / period, adjust=False).mean()
        roll_down = pd.Series(down, index=close.index).ewm(alpha=1 / period, adjust=False).mean()
        rs = roll_up / roll_down.replace(0, np.nan)
        return (100 - (100 / (1 + rs))).fillna(50)

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> pd.Series:
        prev_close = df["close"].shift(1)
        tr = pd.concat([
            (df["high"] - df["low"]).abs(),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean()

    def _spread_ok(self) -> bool:
        tick = mt5.symbol_info_tick(self.config.symbol)
        info = mt5.symbol_info(self.config.symbol)
        if tick is None or info is None:
            return False
        spread_points = int(round((tick.ask - tick.bid) / info.point))
        if spread_points > self.config.max_spread_points:
            log.info("Spread te hoog: %s > %s points", spread_points, self.config.max_spread_points)
            return False
        return True

    def _signal(self, df_entry: pd.DataFrame, df_trend: pd.DataFrame) -> Tuple[str, float]:
        close = df_entry["close"]
        ema_fast = close.ewm(span=self.config.ema_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.config.ema_slow, adjust=False).mean()
        rsi = self._rsi(close, self.config.rsi_period)
        atr = self._atr(df_entry, self.config.atr_period)

        trend_close = df_trend["close"]
        trend_ema = trend_close.ewm(span=self.config.ema_trend, adjust=False).mean().iloc[-1]
        trend_price = trend_close.iloc[-1]

        bullish_trend = trend_price > trend_ema
        bearish_trend = trend_price < trend_ema

        fast = ema_fast.iloc[-1]
        slow = ema_slow.iloc[-1]
        rsi_last = rsi.iloc[-1]
        atr_last = float(atr.iloc[-1])

        if fast > slow and rsi_last >= self.config.rsi_buy_threshold and bullish_trend:
            return "buy", atr_last
        if fast < slow and rsi_last <= self.config.rsi_sell_threshold and bearish_trend:
            return "sell", atr_last
        return "hold", atr_last

    def _positions_count(self) -> int:
        positions = mt5.positions_get(symbol=self.config.symbol)
        return len(positions) if positions else 0

    def _calc_volume(self, sl_distance_price: float) -> float:
        account = mt5.account_info()
        info = mt5.symbol_info(self.config.symbol)
        if account is None or info is None:
            raise RuntimeError("Kon account/symbol info niet ophalen voor volume berekening")

        risk_amount = float(account.equity) * (abs(self.config.risk_per_trade_pct) / 100.0)
        tick_value = float(info.trade_tick_value or 0.0)
        tick_size = float(info.trade_tick_size or 0.0)
        if tick_value <= 0 or tick_size <= 0 or sl_distance_price <= 0:
            return max(float(info.volume_min), 0.01)

        cost_per_lot = (sl_distance_price / tick_size) * tick_value
        raw_lots = risk_amount / cost_per_lot if cost_per_lot > 0 else float(info.volume_min)

        step = float(info.volume_step or 0.01)
        vol_min = float(info.volume_min or 0.01)
        vol_max = float(info.volume_max or 100.0)

        rounded = np.floor(raw_lots / step) * step
        final = float(np.clip(rounded, vol_min, vol_max))
        return round(final, 2)

    def _place_order(self, side: str, atr_value: float) -> None:
        info = mt5.symbol_info(self.config.symbol)
        tick = mt5.symbol_info_tick(self.config.symbol)
        if info is None or tick is None:
            raise RuntimeError(f"Geen symbol/tick info: {mt5.last_error()}")

        sl_distance = atr_value * self.config.atr_sl_multiple
        tp_distance = atr_value * self.config.atr_tp_multiple
        volume = self._calc_volume(sl_distance)

        if side == "buy":
            price = tick.ask
            sl = price - sl_distance
            tp = price + tp_distance
            order_type = mt5.ORDER_TYPE_BUY
        else:
            price = tick.bid
            sl = price + sl_distance
            tp = price - tp_distance
            order_type = mt5.ORDER_TYPE_SELL

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.config.symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": round(sl, info.digits),
            "tp": round(tp, info.digits),
            "deviation": self.config.deviation,
            "magic": self.config.magic,
            "comment": "multi-filter-bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"Order mislukt ({side}): {result}")

        self.last_trade_ts = time.time()
        log.info("Order OK: %s %.2f lots @ %.5f", side.upper(), volume, price)

    def _can_trade_now(self) -> bool:
        if self._daily_loss_exceeded():
            return False
        if time.time() - self.last_trade_ts < self.config.cooldown_seconds:
            return False
        if not self._spread_ok():
            return False
        if self._positions_count() >= self.config.max_open_positions:
            return False
        return True

    def tick_once(self) -> str:
        if not self._can_trade_now():
            return "guard_blocked"

        df_entry = self._rates(self.config.timeframe)
        df_trend = self._rates(self.config.trend_timeframe)
        signal, atr_value = self._signal(df_entry, df_trend)

        if signal in ("buy", "sell"):
            self._place_order(signal, atr_value)
            return signal
        return "hold"

    def run(self, stop_event: Optional[Event] = None) -> None:
        self.connect()
        log.info("Bot gestart op %s", self.config.symbol)

        while True:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                self.tick_once()
            except Exception as exc:
                log.exception("Loop fout: %s", exc)
            time.sleep(self.config.poll_seconds)

        self.shutdown()


def main() -> None:
    bot = MT5ForexBot(BotConfig())
    bot.run()


if __name__ == "__main__":
    main()
