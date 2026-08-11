"""The console's view of the radio: read off-thread, and unable to break anything."""

from __future__ import annotations

import logging
import threading

from vmd.background import BackgroundValue
from vmd.radio.airos import AirOsRadio, LinkStatus, RadioError
from vmd.settings import Settings

logger = logging.getLogger(__name__)

# How often the radio is actually asked. Reading it more than once a few seconds
# tells nobody anything new and costs the radio a login each time.
CACHE_SECONDS = 4.0

# What the status line has to show before the radio has ever answered. Never a
# blank and never a dash: both of those are how this console says "the radio has
# nothing to report", and a radio nobody has managed to reach yet is a different
# thing entirely.
CHECKING = "checking the radio"


class RadioService:
    """One radio, asked on a thread of its own.

    The reading used to be taken on whatever thread called `status`, which in the
    console is the thread that draws the window, on a two-second heartbeat. An
    unreachable radio costs about 12 s of login timeouts before it will say so,
    and while that ran the window did not repaint, the supervisor did not tick,
    and the alarm strip could not appear. The radio being unreachable and the
    perimeter needing watching are not independent events - a link that has
    dropped is exactly when both happen - so this was the console going blind at
    the moment it was needed.

    `status` therefore answers from what was last read and never waits. What it
    must never do is present that as the state of the world now: a reading that
    has gone stale carries its age, and a radio that has not answered at all
    says so in as many words.
    """

    def __init__(self, settings: Settings) -> None:
        self._lock = threading.Lock()
        self._reading: BackgroundValue[dict] | None = None
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
            previous, self._reading = self._reading, None
            if self.radio is not None:
                self._reading = BackgroundValue(
                    read=_reader(self.radio),
                    stale_after=CACHE_SECONDS,
                    name="the radio",
                )
        # Outside the lock: closing waits on a thread, and a thread that is
        # mid-read would be waiting for this same lock.
        if previous is not None:
            previous.close()

    def close(self) -> None:
        """Let the reader go. Bounded inside, and never raises."""
        with self._lock:
            reading, self._reading = self._reading, None
        if reading is not None:
            reading.close()

    def status(self) -> dict:
        """What the radio last said, with its age. Never waits for the radio."""
        with self._lock:
            source = self._reading
            enabled = self.settings.radio.enabled
        if source is None:
            return {
                "connected": False,
                "reason": "the radio is not set up" if not enabled else "no radio address set",
            }
        reading = source.get()
        if not reading.known or reading.value is None:
            return {"connected": False, "checking": True, "reason": CHECKING}
        payload = dict(reading.value)
        payload["age_seconds"] = reading.age
        return payload


def _reader(radio: AirOsRadio):
    """One read of one radio, with every failure turned into a sentence.

    Bound to the radio object rather than to the service, so that a save which
    replaces the radio cannot have a read already in flight write its answer
    about the old address into the new one's reading.
    """

    def read() -> dict:
        try:
            status: LinkStatus = radio.status()
            return status.as_dict()
        except RadioError as exc:
            return {"connected": False, "reason": str(exc)}
        except Exception as exc:  # noqa: BLE001 - the console outlives the radio
            logger.exception("could not read the radio")
            return {"connected": False, "reason": str(exc)}

    return read
