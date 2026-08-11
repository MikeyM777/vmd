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
        # Whether the caller supplied the time matters below: a test drives this
        # clock and must keep driving it, while the console must be timed by the
        # clock that actually ran during the read.
        supplied = now is not None
        now = time.monotonic() if not supplied else now
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
            # When the read finished, not when it started. A radio that is not
            # answering takes both login attempts' timeouts to say so - longer
            # than the cache window - so stamping the cache with the time before
            # the call left it already expired the moment it was written, and
            # the console's two-second heartbeat went straight back into another
            # blocking read. The window then froze for as long as the radio
            # stayed down, which is exactly when the operator needs the Settings
            # tab to find out why.
            self._cached_at = now if supplied else time.monotonic()
            return payload
