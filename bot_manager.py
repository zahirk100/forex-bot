import threading
import time
from typing import Any, Dict

from mt5_bot import BotConfig, MT5ForexBot


class BotManager:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._bot: MT5ForexBot | None = None
        self._last_error: str | None = None
        self._started_at: float | None = None

    def start(self) -> Dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return {"ok": True, "message": "Bot draait al"}

        self._stop_event = threading.Event()
        self._bot = MT5ForexBot(BotConfig())
        self._last_error = None
        self._started_at = time.time()

        def runner() -> None:
            try:
                self._bot.run(stop_event=self._stop_event)
            except Exception as exc:
                self._last_error = str(exc)

        self._thread = threading.Thread(target=runner, daemon=True)
        self._thread.start()
        return {"ok": True, "message": "Bot gestart"}

    def stop(self) -> Dict[str, Any]:
        if not self._thread or not self._thread.is_alive() or not self._stop_event:
            return {"ok": True, "message": "Bot is al gestopt"}

        self._stop_event.set()
        self._thread.join(timeout=10)
        return {"ok": True, "message": "Stop signaal gestuurd"}

    def status(self) -> Dict[str, Any]:
        running = bool(self._thread and self._thread.is_alive())
        uptime = None
        if running and self._started_at:
            uptime = round(time.time() - self._started_at, 1)

        config = self._bot.config.to_public_dict() if self._bot else None
        return {
            "running": running,
            "uptime_seconds": uptime,
            "last_error": self._last_error,
            "config": config,
        }
