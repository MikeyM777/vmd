"""The console's view of the radio: cached, and unable to break anything."""

from __future__ import annotations

import logging
import threading
import time

from vmd.radio.airos import AirOsRadio, LinkStatus, RadioError
from vmd.settings import Settings

logger = logging.getLogger(__name__)

# The radio is polled by every open console page. Reading it more than once a
# few seconds tells nobody anything new and costs the radio a login each time.
CACHE_SECONDS = 4.0


class RadioService:
    def __init__(self, settings: Settings) -> None:
        self._lock = threading.Lock()
        self._cached: dict | None = None
        self._cached_at = 0.0
        self.apply(settings)

    def apply(self, settings: Settings) -> None:
        with self._lock:
            self.settings = settings
            radio = settings.radio
            self.radio = (
                AirOsRadio(radio.host, radio.username, radio.password)
                if radio.enabled and radio.host.strip()
                else None
            )
            self._cached = None

    def status(self, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        with self._lock:
            if self.radio is None:
                enabled = self.settings.radio.enabled
                return {
                    "connected": False,
                    "reason": "the radio is not set up"
                    if not enabled
                    else "no radio address set",
                }
            if self._cached is not None and now - self._cached_at < CACHE_SECONDS:
                return self._cached

            try:
                status: LinkStatus = self.radio.status()
                payload = status.as_dict()
            except RadioError as exc:
                payload = {"connected": False, "reason": str(exc)}
            except Exception as exc:  # noqa: BLE001 - the console outlives the radio
                logger.exception("could not read the radio")
                payload = {"connected": False, "reason": str(exc)}

            self._cached = payload
            self._cached_at = now
            return payload
