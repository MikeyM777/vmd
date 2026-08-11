"""The console's PTZ: one camera, held open, and never able to break the console."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from vmd.ptz.encoder import CameraEncoders, apply_budget
from vmd.ptz.onvif import OnvifPtz, PtzError
from vmd.settings import Settings

logger = logging.getLogger(__name__)

# How long a closing window will wait for the sender to finish what it is doing.
# Long enough for one command against a camera that is answering, short enough
# that closing the console never feels like a hang. The thread is a daemon, so
# abandoning it costs nothing beyond this point.
CLOSE_SECONDS = 2.0

# How long a command may be outstanding before the console stops implying the
# camera is doing what it was asked. Longer than a healthy command takes and
# shorter than the timeout on an unreachable one, so the operator learns that
# the camera has gone quiet before the failure itself comes back.
UNANSWERED_AFTER = 1.5


class PtzService:
    """Wraps the camera connection with the two things the console needs:
    a lock, because a browser sends overlapping commands as keys are held and
    released, and a rule that no failure ever escapes as an exception."""

    def __init__(self, settings: Settings) -> None:
        self._lock = threading.Lock()
        self.apply(settings)

    def apply(self, settings: Settings) -> None:
        with self._lock:
            self.settings = settings
            host = settings.camera.host.strip()
            self.camera = (
                OnvifPtz(host, settings.camera.username, settings.camera.password) if host else None
            )
            self._connected = False

    def status(self) -> dict:
        with self._lock:
            if self.camera is None:
                return {"available": False, "reason": "no camera address set"}
            if not self._connected:
                capability = self.camera.connect()
                self._connected = capability.available
            payload = self.camera.capability.as_dict()
            if self._connected:
                # Where the head actually is, when the camera will say. Cameras
                # that do not answer GetStatus simply have no figure, which the
                # console shows as "—" rather than as a number it invented.
                position = self.camera.position()
                if position:
                    payload.update(position)
            return payload

    def _do(self, action: str, work) -> dict:
        with self._lock:
            if self.camera is None:
                return {"ok": False, "error": "no camera address set"}
            try:
                work()
                return {"ok": True}
            except PtzError as exc:
                logger.warning("ptz %s failed: %s", action, exc)
                return {"ok": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - the console outlives the camera
                logger.exception("ptz %s failed unexpectedly", action)
                return {"ok": False, "error": str(exc)}

    def encoders(self) -> dict:
        """What the camera is currently encoding, or why we cannot tell."""
        with self._lock:
            if self.camera is None:
                return {"ok": False, "error": "no camera address set"}
            try:
                configs = CameraEncoders(self.camera).read()
            except PtzError as exc:
                return {"ok": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001
                logger.exception("could not read encoder settings")
                return {"ok": False, "error": str(exc)}
            payload = []
            encoders = CameraEncoders(self.camera)
            for config in configs:
                try:
                    sizes = encoders.options(config.token)
                except Exception:  # noqa: BLE001 - options are a nicety, not the point
                    sizes = []
                entry = config.as_dict()
                entry["available_sizes"] = [list(size) for size in sizes]
                entry["label"] = config.label
                payload.append(entry)
            return {"ok": True, "configs": payload, "labels": [c.label for c in configs]}

    def set_encoder(self, token, width=None, height=None, kbps=None, fps=None) -> dict:
        """Change one encoder setting, sending every other field back unchanged.

        This is how a 4K stream stops being 4K. Nothing downstream can do it:
        the console shows what the camera sends, and the link has already been
        paid for by the time it arrives.
        """
        with self._lock:
            if self.camera is None:
                return {"ok": False, "error": "no camera address set"}
            try:
                encoders = CameraEncoders(self.camera)
                config = next((c for c in encoders.read() if c.token == token), None)
                if config is None:
                    return {"ok": False, "error": f"the camera has no encoder called {token}"}
                size = (int(width), int(height)) if width and height else None
                updated = encoders.apply(
                    config,
                    kbps=int(kbps) if kbps else None,
                    size=size,
                    fps=int(fps) if fps else None,
                )
                return {
                    "ok": True,
                    "label": updated.label,
                    "note": "the camera keeps this; restart the console to pull the new size",
                }
            except PtzError as exc:
                return {"ok": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - the console outlives the camera
                logger.exception("could not change encoder settings")
                return {"ok": False, "error": str(exc)}

    def fit_encoders_to_link(self, ceiling_kbps: int) -> dict:
        """Cap every stream so their total fits the link, and report what landed.

        Both the button on the Settings tab and the automatic loop come through
        here, deliberately: "fit the camera to the link" is one operation, and
        two implementations of it would be two answers to the same question
        within a month - one of them checked and one of them not.

        Slow, and it holds this service's lock while it runs: several ONVIF
        calls across a radio link. It must never be called on the thread that
        draws the window. `BitrateLoop` hands it to an executor and the Settings
        tab runs it in a `_ToolJob`.
        """
        with self._lock:
            if self.camera is None:
                return {"ok": False, "error": "no camera address set"}
            try:
                result = apply_budget(CameraEncoders(self.camera), ceiling_kbps)
            except PtzError as exc:
                return {"ok": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - the console outlives the camera
                logger.exception("could not set encoder settings")
                return {"ok": False, "error": str(exc)}
            if not result.get("ok"):
                return result
            said = list(result.get("changed") or [])
            for token in result.get("refused") or []:
                # Said in the answer and not only in the log, because this is
                # what the operator pressed the button to find out. A camera
                # that answers 200 and keeps its old setting is indistinguishable
                # from a camera that obeyed unless somebody says.
                said.append(f"{token}: the camera did not keep this - it is unchanged")
            result["changed"] = said or ["nothing needed changing"]
            return result

    def move(self, pan: float, tilt: float, zoom: float) -> dict:
        return self._do("move", lambda: self.camera.move(pan, tilt, zoom))

    # Lambdas, not bound methods. `self.camera.stop` is evaluated before `_do`
    # is entered, so on a console with no camera address yet - the state every
    # first run is in - it raised AttributeError on None instead of returning
    # the sentence `_do` exists to return. That escapes into a Qt key handler,
    # which is the one place in this program an exception must never reach.
    def stop(self) -> dict:
        return self._do("stop", lambda: self.camera.stop())

    def home(self) -> dict:
        return self._do("home", lambda: self.camera.home())


@dataclass(frozen=True)
class Answered:
    """One command the camera has finished answering, and what it said."""

    command: tuple
    result: dict


class PtzCommands:
    """Sends PTZ commands on a thread of its own, keeping only the latest.

    Every one of these crosses a radio link to a camera that answers when it
    feels like it. Measured against an address with nothing at the far end, one
    tap of an arrow key cost 12.36 s - 6.19 s for the press and 6.17 s for the
    release - and all of it was spent inside a Qt key handler. While that ran
    the window did not repaint, the supervisor did not tick and the alarm strip
    could not appear, so movement on the perimeter during the freeze was simply
    missed. That is why this is a thread and not a smaller timeout.

    A latest-value mailbox, not a queue. The operator taps four arrows while one
    command is on the wire; replaying all four would have the head performing a
    gesture that finished seconds ago. Only the last one is still true, so only
    the last one is sent - with one exception that is not an optimisation but
    the whole safety property of this file:

        A stop is never dropped.

    Coalescing is last-wins, and a stop is only ever superseded by a command the
    operator asked for afterwards - pressing a key again. Anything already
    waiting to be sent when a stop arrives is replaced by that stop, and a stop
    that is still owed when the console closes is delivered before the thread is
    let go. The head must never be left slewing with no key held, including when
    the command that started it is still in flight.
    """

    def __init__(self, ptz, name: str = "ptz") -> None:
        self._ptz = ptz
        # Guards the mailbox, the in-flight marker and the last answer alike:
        # they are read together and must not disagree with each other.
        self._lock = threading.Lock()
        self._wanted: tuple | None = None
        self._sending: tuple | None = None
        self._sending_since: float | None = None
        self._answered: Answered | None = None
        self._wake = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._closing = threading.Event()
        self._name = name
        # Not held under `_lock`: starting a thread must never happen while the
        # sender might be waiting for that same lock.
        self._threading = threading.Lock()
        self._thread = self._start_thread()

    # ------------------------------------------------------------- the mailbox

    def submit(self, command: tuple) -> None:
        """Ask for a command. Returns at once, whatever the camera is doing."""
        if self._closing.is_set():
            logger.debug("%s: not sending %s; the sender is closing", self._name, command)
            return
        with self._lock:
            self._wanted = command
            self._idle.clear()
        # After the mailbox is filled, so a sender that wakes finds the work.
        self._ensure_thread()
        self._wake.set()

    def move(self, pan: float, tilt: float, zoom: float) -> None:
        self.submit(("move", pan, tilt, zoom))

    def stop(self) -> None:
        self.submit(("stop",))

    def home(self) -> None:
        self.submit(("home",))

    # -------------------------------------------------------------- what it is

    def last_answer(self) -> Answered | None:
        with self._lock:
            return self._answered

    def unanswered_for(self) -> float | None:
        """How long the camera has been sitting on a command, or None.

        None means nothing is outstanding - not that everything is well, which
        is what `last_answer` is for.
        """
        with self._lock:
            if self._sending_since is None:
                return None
            return max(0.0, time.monotonic() - self._sending_since)

    def wait_until_idle(self, timeout: float) -> bool:
        """Wait until nothing is queued or in flight. Bounded, always."""
        return self._idle.wait(timeout)

    # -------------------------------------------------------------- lifecycle

    def close(self, timeout: float = CLOSE_SECONDS) -> bool:
        """Deliver whatever is still owed, then let the thread go.

        Bounded, and the thread is a daemon: a camera that never answers costs
        this much of a closing window and not one second more. `concurrent
        .futures` is deliberately not used anywhere here - its atexit hook joins
        worker threads at interpreter exit, which is exactly how a console that
        had already closed its window went on to hang.
        """
        self._closing.set()
        self._wake.set()
        thread = self._thread
        thread.join(timeout)
        if thread.is_alive():
            logger.warning(
                "%s: the camera did not answer in time; letting the sender go", self._name
            )
            return False
        return True

    def _start_thread(self) -> threading.Thread:
        thread = threading.Thread(target=self._run, name=f"{self._name}-commands", daemon=True)
        thread.start()
        return thread

    def _ensure_thread(self) -> None:
        """Put the sender back if it has been lost.

        Nothing inside `_run` raises - each command is guarded - so this is for
        what a guard cannot catch. Without it, one lost thread would mean every
        stop after it was dropped, and a dropped stop is a head that keeps
        slewing.
        """
        with self._threading:
            if self._closing.is_set() or self._thread.is_alive():
                return
            logger.error("%s: the command sender was lost; starting another", self._name)
            self._thread = self._start_thread()

    def _run(self) -> None:
        while True:
            with self._lock:
                command, self._wanted = self._wanted, None
                self._sending = command
                self._sending_since = time.monotonic() if command is not None else None
                if command is None:
                    self._idle.set()
            if command is not None:
                self._deliver(command)
                continue
            if self._closing.is_set():
                return
            self._wake.wait()
            self._wake.clear()

    def _deliver(self, command: tuple) -> None:
        result = self._send(command)
        with self._lock:
            self._answered = Answered(command=command, result=result)
            self._sending = None
            self._sending_since = None

    def _send(self, command: tuple) -> dict:
        kind = command[0]
        try:
            if kind == "move":
                return self._ptz.move(command[1], command[2], command[3])
            if kind == "stop":
                return self._ptz.stop()
            if kind == "home":
                return self._ptz.home()
        except Exception as exc:  # noqa: BLE001 - the console outlives the camera
            logger.exception("ptz %s failed unexpectedly", kind)
            return {"ok": False, "error": str(exc)}
        logger.error("%s: nothing knows how to send %s", self._name, command)
        return {"ok": False, "error": f"unknown camera command {kind}"}
