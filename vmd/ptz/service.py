"""The console's PTZ: one camera, held open, and never able to break the console."""

from __future__ import annotations

import logging
import threading

from vmd.ptz.encoder import CameraEncoders, fit_to_link
from vmd.ptz.onvif import OnvifPtz, PtzError
from vmd.settings import Settings

logger = logging.getLogger(__name__)


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
        """Cap every stream so their total fits the link, and report what changed."""
        with self._lock:
            if self.camera is None:
                return {"ok": False, "error": "no camera address set"}
            try:
                encoders = CameraEncoders(self.camera)
                configs = encoders.read()
                if not configs:
                    return {"ok": False, "error": "the camera reported no encoder settings"}
                targets = fit_to_link(configs, ceiling_kbps)
                changed = []
                for config in configs:
                    target = targets.get(config.token)
                    if target is None or config.bitrate_kbps == target:
                        continue
                    encoders.cap_bitrate(config, target)
                    changed.append(f"{config.name or config.token}: "
                                   f"{config.bitrate_kbps or 'uncapped'} -> {target} kb/s")
                return {"ok": True, "changed": changed or ["nothing needed changing"]}
            except PtzError as exc:
                return {"ok": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - the console outlives the camera
                logger.exception("could not set encoder settings")
                return {"ok": False, "error": str(exc)}

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
