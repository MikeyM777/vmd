"""The console's PTZ: one camera, held open, and never able to break the console."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from vmd.ptz.encoder import CameraEncoders, apply_budget
from vmd.ptz.lenses import NOT_ASKED, Lenses
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
        # Two locks, and which one guards what is the whole of it.
        #
        # `_lock` means "one call to the camera at a time". It is held across
        # ONVIF round trips, which on this link take seconds, so anything that
        # takes it may be waiting for the far end of a radio hop.
        #
        # `_swap` means "which camera we are talking to". It is never held
        # across anything that touches the wire, so taking it is always
        # instantaneous. `apply` is the only writer, and `apply` is called from
        # the Save slot - on the thread that draws the window. It used to take
        # `_lock`, which made pressing Save a wait on whatever the camera
        # happened to be doing: a zoom readback holds `_lock` for one call per
        # lens, each of which may take the full eight seconds the timeout
        # allows. A frozen Qt handler is a window that does not repaint and an
        # alarm strip that cannot appear, which is the fault the whole command
        # thread exists to prevent, arriving by a different door.
        self._lock = threading.Lock()
        self._swap = threading.Lock()
        self.apply(settings)

    def apply(self, settings: Settings) -> None:
        """Point at whatever camera the operator has just saved. Never waits.

        The new camera and its lens map are built first and swapped in
        afterwards, because building them touches nothing: `OnvifPtz.__init__`
        and `Lenses.__init__` only remember what they were given, and the first
        call to the wire happens later, on the worker that makes it.

        A call already out on the wire against the old camera is left to finish
        against the old camera. It is bounded by the ONVIF timeout, its answer
        goes into an object nothing reads any more, and waiting for it here
        would be the freeze this is written to remove.
        """
        host = settings.camera.host.strip()
        camera = (
            OnvifPtz(host, settings.camera.username, settings.camera.password) if host else None
        )
        # Which lens is which picture, remade whenever the camera is. The
        # mapping is a property of THIS camera at THIS address; carrying an
        # old one across a settings change would point the thermal zoom at
        # a profile token from a camera that is no longer there.
        lenses = (
            Lenses(camera, [stream.name for stream in settings.camera.streams])
            if camera is not None
            else None
        )
        with self._swap:
            self.settings = settings
            self.camera = camera
            self._connected = False
            self.lenses = lenses

    # Every method below takes the camera into a local before it uses it, and
    # that is the price of `apply` no longer waiting: the camera can be swapped
    # while a call is inside one of these, and each of them touched
    # `self.camera` more than once. Read twice, the second read can be None, and
    # `status` is the one with no guard around it - a save landing between two
    # of its lines raised AttributeError into whoever asked, which is the shape
    # of thing that must never reach a Qt handler. Held in a local, the answer
    # describes the camera the question was asked of, and the next call
    # describes the one there is now.
    def status(self) -> dict:
        with self._lock:
            camera = self.camera
            if camera is None:
                return {"available": False, "reason": "no camera address set"}
            if not self._connected:
                capability = camera.connect()
                self._connected = capability.available
            payload = camera.capability.as_dict()
            if self._connected:
                # Where the head actually is, when the camera will say. Cameras
                # that do not answer GetStatus simply have no figure, which the
                # console shows as "—" rather than as a number it invented.
                position = camera.position()
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
            camera = self.camera
            if camera is None:
                return {"ok": False, "error": "no camera address set"}
            try:
                configs = CameraEncoders(camera).read()
            except PtzError as exc:
                return {"ok": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001
                logger.exception("could not read encoder settings")
                return {"ok": False, "error": str(exc)}
            payload = []
            encoders = CameraEncoders(camera)
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
            camera = self.camera
            if camera is None:
                return {"ok": False, "error": "no camera address set"}
            try:
                encoders = CameraEncoders(camera)
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

        Slow: half a dozen ONVIF calls across a radio link. It must never be
        called on the thread that draws the window - `BitrateLoop` hands it to
        an executor and the Settings tab runs it in a `_ToolJob`.

        The lock is taken per call to the camera rather than across the whole
        operation, and that is a safety property rather than a nicety. Held for
        the lot, a stop arriving in the middle of a fit waits for the entire
        exchange - and the head goes on slewing with no key held for as long as
        that takes. Survivable while the only thing that fitted the camera was
        a button somebody pressed; not survivable now that a loop does it by
        itself. See `_OneAtATime`, and `PtzCommands`, whose whole safety
        property is that a stop gets through.
        """
        with self._lock:
            camera = self.camera
        if camera is None:
            return {"ok": False, "error": "no camera address set"}
        try:
            result = apply_budget(_OneAtATime(CameraEncoders(camera), self._lock), ceiling_kbps)
        except PtzError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - the console outlives the camera
            logger.exception("could not set encoder settings")
            return {"ok": False, "error": str(exc)}
        if not result.get("ok"):
            return result
        said = list(result.get("changed") or [])
        for token in result.get("refused") or []:
            # Said in the answer and not only in the log, because this is what
            # the operator pressed the button to find out. A camera that answers
            # 200 and keeps its old setting is indistinguishable from a camera
            # that obeyed unless somebody says.
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

    # ------------------------------------------------------------------- zoom
    #
    # Separate from `move` because the camera is two lenses on one gimbal.
    # `move`'s zoom argument goes wherever the head goes, which was fine when
    # there was one picture; these address a named picture, and the pan and tilt
    # are deliberately left out of them - see `Lenses`.

    def zoom(self, stream: str, where: float) -> dict:
        """Send one picture's lens to a zoom, 0.0 wide to 1.0 tele."""
        with self._lock:
            lenses = self.lenses
            if lenses is None:
                return {"ok": False, "error": "no camera address set"}
            try:
                answer = lenses.go_to(stream, where)
            except Exception as exc:  # noqa: BLE001 - the console outlives the camera
                logger.exception("could not zoom %s", stream)
                return {"ok": False, "error": str(exc)}
        return {"ok": answer["ok"], "error": answer["reason"]}

    def zoom_hold(self, stream: str, speed: float) -> dict:
        """Keep one picture's lens zooming, or stop it when the speed is zero."""
        with self._lock:
            lenses = self.lenses
            if lenses is None:
                return {"ok": False, "error": "no camera address set"}
            try:
                answer = lenses.creep(stream, speed)
            except Exception as exc:  # noqa: BLE001
                logger.exception("could not zoom %s", stream)
                return {"ok": False, "error": str(exc)}
        return {"ok": answer["ok"], "error": answer["reason"]}

    def zoom_poll(self) -> None:
        """Read back whichever lenses are worth reading right now.

        Slow - it may cross the link - so it belongs on the same worker the
        other camera calls use, never on the thread that draws the window.
        `Lenses` decides whether there is anything to do, and on a link nobody
        is zooming the answer is almost always nothing.
        """
        with self._lock:
            lenses = self.lenses
            if lenses is None:
                return
            try:
                lenses.poll()
            except Exception:  # noqa: BLE001
                logger.exception("could not read the zoom positions")

    def zoom_position(self, stream: str) -> float | None:
        """Where a lens was last seen to be. Never talks to the camera.

        This is what the screen calls on every redraw, which is the whole reason
        it is separate from `zoom_poll`.
        """
        lenses = self.lenses
        return None if lenses is None else lenses.position(stream)

    def zoom_ready(self) -> dict:
        """What the zoom controls should look like before anybody touches them."""
        lenses = self.lenses
        if lenses is None:
            return {"ok": False, "checking": False, "absolute": False, "shared": False,
                    "reason": "no camera address set"}
        return {
            "ok": lenses.reason == "ready",
            # Nobody has asked yet, which lasts a heartbeat or two after every
            # start-up and is not a fault. Every other reason for having no
            # answer is one, and the screen has to be able to tell them apart.
            "checking": lenses.reason == NOT_ASKED,
            "absolute": lenses.absolute(),
            "shared": lenses.shared(),
            "reason": lenses.reason,
        }


class ZoomHandle:
    """What a zoom bar on the screen is given, and the whole of what it may do.

    Three methods, all of them returning at once. That is the point of it: the
    widget is on the thread that draws the window, every one of these can cross
    a radio link whose last measured round trip was two seconds, and a console
    that freezes for two seconds is a console that is not showing the perimeter.
    So asking is a message to the sender, and reading is a cached number that
    was fetched by somebody else.

    It exists as its own object rather than as three methods on the tab because
    the tab should not know that a camera has profiles, or that there is a
    thread, or that the reading and the commanding go different ways.
    """

    def __init__(self, commands: "PtzCommands") -> None:
        self._commands = commands

    def go_to(self, stream: str, where: float) -> None:
        self._commands.zoom(stream, where)

    def creep(self, stream: str, speed: float) -> None:
        self._commands.zoom_hold(stream, speed)

    def position(self, stream: str) -> float | None:
        return self._commands.zoom_position(stream)

    def poll(self) -> None:
        """Refresh the readouts if any are due. For the console's heartbeat."""
        self._commands.poll_zoom()


class _OneAtATime:
    """A `CameraEncoders` whose every call to the camera is taken in turn.

    One connection to the camera at a time, exactly as before - but the turn is
    ONE call rather than the whole operation, so anything else waiting for the
    camera gets in between them. What is waiting is a stop, and a stop that
    waits is a head still moving with no key held.

    Not thread-safety for its own sake: `OnvifPtz` is not written to be spoken
    to by two threads at once and this does not make it so. It only makes the
    queue in front of it a short one.
    """

    def __init__(self, encoders: CameraEncoders, lock: threading.Lock) -> None:
        self._encoders = encoders
        self._lock = lock

    def read(self):
        with self._lock:
            return self._encoders.read()

    def limits(self, token: str):
        with self._lock:
            return self._encoders.limits(token)

    def cap_bitrate(self, config, kbps: int):
        with self._lock:
            return self._encoders.cap_bitrate(config, kbps)


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

    **There is one mailbox per lane, and there are two lanes.** Steering and
    zoom are separate motors, and since the camera turned out to be two lenses
    on one gimbal they are also separate intentions: the operator pans with the
    arrow keys while dragging a zoom slider, and one latest-value slot shared
    between them would have each throwing the other away. Whichever he touched
    last would happen and the other would silently not - which looks exactly
    like a command lost over the radio link, the failure this console has spent
    the most time chasing.

    Lanes coalesce within themselves and never across. The camera is still
    spoken to one call at a time, and steering is always drained first, so the
    stop guarantee above is untouched: a stop can wait behind at most one
    zoom that is already on the wire, which is the same single call a bitrate
    write already costs it.

    There is a lane per LENS, not one for zoom altogether. Dragging the thermal
    slider and then the visible one is two intentions about two pictures, and a
    single zoom slot would have the second silently discard the first - the same
    mistake as sharing one slot with steering, one level down. The number of
    lanes is bounded by the number of pictures, which is two.
    """

    # Steering is drained before anything else, always. See the stop guarantee.
    STEER_LANE = "steer"

    def __init__(self, ptz, name: str = "ptz") -> None:
        self._ptz = ptz
        # Guards the mailbox, the in-flight marker and the last answer alike:
        # they are read together and must not disagree with each other.
        self._lock = threading.Lock()
        self._wanted: dict[str, tuple] = {}
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

    def submit(self, command: tuple, lane: str = "steer") -> None:
        """Ask for a command. Returns at once, whatever the camera is doing."""
        if self._closing.is_set():
            logger.debug("%s: not sending %s; the sender is closing", self._name, command)
            return
        with self._lock:
            self._wanted[lane] = command
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

    def zoom(self, stream: str, where: float) -> None:
        self.submit(("zoom", stream, where), lane=f"zoom:{stream}")

    def zoom_hold(self, stream: str, speed: float) -> None:
        self.submit(("zoom_hold", stream, speed), lane=f"zoom:{stream}")

    def poll_zoom(self) -> None:
        """Ask the sender to refresh the zoom readouts, if any are due.

        Safe to call on every heartbeat: `Lenses` decides whether there is
        anything to do and on a link nobody is zooming the answer is nothing.
        It goes through the sender rather than being called directly because
        when there IS something to do it crosses the radio link, and the thread
        that draws the window may not wait two seconds for a slider.
        """
        self.submit(("zoom_poll",), lane="poll")

    def zoom_position(self, stream: str) -> float | None:
        """The last zoom position seen, from the cache. Sends nothing."""
        reader = getattr(self._ptz, "zoom_position", None)
        return None if reader is None else reader(stream)

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
                # Steering first and unconditionally: a stop waiting behind a
                # zoom slider being dragged is a head still slewing. Everything
                # else goes in the order it was asked for, which a dict has
                # preserved since Python 3.7 and which matters here - the
                # operator's second thought about a lens should not be sent
                # before his first thought about the other one.
                command = None
                if self.STEER_LANE in self._wanted:
                    command = self._wanted.pop(self.STEER_LANE)
                elif self._wanted:
                    command = self._wanted.pop(next(iter(self._wanted)))
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

    # Commands the operator did not ask for, whose answers must not be shown to
    # him as the state of his steering. The console tells him the camera has
    # gone quiet by looking at the LAST answer; a background refresh succeeding
    # in between two failed arrow keys would wipe that out and the camera would
    # look fine while nothing he pressed was working.
    QUIET = frozenset({"zoom_poll"})

    def _deliver(self, command: tuple) -> None:
        result = self._send(command)
        with self._lock:
            if command[0] not in self.QUIET:
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
            if kind == "zoom":
                return self._ptz.zoom(command[1], command[2])
            if kind == "zoom_hold":
                return self._ptz.zoom_hold(command[1], command[2])
            if kind == "zoom_poll":
                self._ptz.zoom_poll()
                return {"ok": True}
        except Exception as exc:  # noqa: BLE001 - the console outlives the camera
            logger.exception("ptz %s failed unexpectedly", kind)
            return {"ok": False, "error": str(exc)}
        logger.error("%s: nothing knows how to send %s", self._name, command)
        return {"ok": False, "error": f"unknown camera command {kind}"}
