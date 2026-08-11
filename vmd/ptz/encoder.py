"""Reading and capping the camera's own encoder settings, over ONVIF.

Why this exists: panning floods the link. A stream that fits while the scene is
still produces several times as much data the moment the camera moves, because
every macroblock changes at once. On a radio link with a few megabits to give,
that spike does not just degrade the picture being panned - it starves every
other stream sharing the link, which is why moving the head knocked the thermal
out while it recovered on its own the moment the head stopped.

The fix belongs at the camera: cap the bitrate so the encoder throws away
quality instead of bandwidth it does not have. Nothing on this side can do that,
because by the time the data reaches the laptop it has already crossed the link.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from vmd.ptz.onvif import MEDIA, OnvifPtz, PtzError, _first, _xml

logger = logging.getLogger(__name__)


@dataclass
class EncoderConfig:
    """One video encoder configuration as the camera reports it."""

    token: str
    name: str
    encoding: str
    width: int | None
    height: int | None
    fps: int | None
    bitrate_kbps: int | None
    quality: float | None
    gov_length: int | None

    def as_dict(self) -> dict:
        return {
            "token": self.token,
            "name": self.name,
            "encoding": self.encoding,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "bitrate_kbps": self.bitrate_kbps,
            "quality": self.quality,
            "gov_length": self.gov_length,
        }

    @property
    def label(self) -> str:
        size = f"{self.width}x{self.height}" if self.width and self.height else "unknown size"
        rate = f"{self.bitrate_kbps} kb/s" if self.bitrate_kbps else "no bitrate limit"
        return f"{self.name or self.token}: {size} {self.encoding} {self.fps or '?'} fps, {rate}"


def _int(text: str | None) -> int | None:
    try:
        return int(float(text)) if text is not None else None
    except ValueError:
        return None


def _float(text: str | None) -> float | None:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def parse_configurations(xml: str) -> list[EncoderConfig]:
    """Pull every VideoEncoderConfiguration out of a GetVideoEncoderConfigurations reply."""
    configs: list[EncoderConfig] = []
    for block in re.findall(
        r"<[^>]*Configurations[^>]*token=\"([^\"]+)\"[^>]*>(.*?)</[^>]*Configurations>", xml, re.DOTALL
    ):
        token, body = block
        configs.append(
            EncoderConfig(
                token=token,
                name=_first(r"<[^>]*Name>(.*?)</[^>]*Name>", body) or "",
                encoding=_first(r"<[^>]*Encoding>(.*?)</[^>]*Encoding>", body) or "",
                width=_int(_first(r"<[^>]*Width>(.*?)</[^>]*Width>", body)),
                height=_int(_first(r"<[^>]*Height>(.*?)</[^>]*Height>", body)),
                fps=_int(_first(r"<[^>]*FrameRateLimit>(.*?)</[^>]*FrameRateLimit>", body)),
                bitrate_kbps=_int(_first(r"<[^>]*BitrateLimit>(.*?)</[^>]*BitrateLimit>", body)),
                quality=_float(_first(r"<[^>]*Quality>(.*?)</[^>]*Quality>", body)),
                gov_length=_int(_first(r"<[^>]*GovLength>(.*?)</[^>]*GovLength>", body)),
            )
        )
    return configs


def parse_options(xml: str) -> list[tuple[int, int]]:
    """The resolutions this camera says it will accept, largest first.

    Asked rather than assumed: a camera that is handed a resolution it does not
    support may refuse, or may accept and produce something else entirely.
    """
    sizes: list[tuple[int, int]] = []
    for block in re.findall(r"<[^>]*ResolutionsAvailable[^>]*>(.*?)</[^>]*ResolutionsAvailable>", xml, re.DOTALL):
        width = _int(_first(r"<[^>]*Width>(.*?)</[^>]*Width>", block))
        height = _int(_first(r"<[^>]*Height>(.*?)</[^>]*Height>", block))
        if width and height and (width, height) not in sizes:
            sizes.append((width, height))
    return sorted(sizes, key=lambda size: size[0] * size[1], reverse=True)


def parse_bitrate_range(xml: str) -> tuple[int | None, int | None]:
    """The lowest and highest bitrate this camera says it will accept.

    It arrives in the same answer the resolutions do and was being thrown away,
    which left this console with no way to know that a target was outside what
    the camera would take. A value outside the range is refused - and this
    camera refuses with an HTTP 200 carrying a SOAP fault, so the refusal does
    not look like one until somebody reads the body.

    Unknown is a state and is reported as one. A camera that names no range is
    a camera with no known minimum, not a camera with an invented one: clamping
    to a number nobody supplied would silently overrule the operator's floor.
    """
    block = _first(r"<[^>]*BitrateRange[^>]*>(.*?)</[^>]*BitrateRange>", xml)
    if block is None:
        return None, None
    return (
        _int(_first(r"<[^>]*Min>(.*?)</[^>]*Min>", block)),
        _int(_first(r"<[^>]*Max>(.*?)</[^>]*Max>", block)),
    )


@dataclass(frozen=True)
class EncoderLimits:
    """What one encoder configuration on this camera will accept.

    `None` for either bound means the camera did not say, which is different
    from there being no bound - see `clamp_bitrate`.
    """

    sizes: list[tuple[int, int]]
    bitrate_min: int | None
    bitrate_max: int | None


def clamp_bitrate(kbps: int, limits: EncoderLimits) -> int:
    """A target the camera will actually take, given what it said it allows.

    A bound the camera did not name is not applied. Being slightly over budget
    on a stream whose camera will not go lower is a worse picture; being outside
    the range is a write that is refused, after which this console would be
    reasoning about a bitrate the camera never had.
    """
    wanted = int(kbps)
    if limits.bitrate_min is not None:
        wanted = max(wanted, limits.bitrate_min)
    if limits.bitrate_max is not None:
        wanted = min(wanted, limits.bitrate_max)
    return wanted


class CameraEncoders:
    """The camera's encoder settings, read and capped."""

    def __init__(self, camera: OnvifPtz) -> None:
        self.camera = camera

    def read(self) -> list[EncoderConfig]:
        xml = self.camera._post(
            "/onvif/media_service", f'<GetVideoEncoderConfigurations xmlns="{MEDIA}"/>'
        )
        return parse_configurations(xml)

    def limits(self, token: str) -> EncoderLimits:
        """Everything one call to the camera says about what it will accept.

        One call rather than two, because it is one answer: the resolutions and
        the bitrate range arrive in the same document, and asking twice over
        this link costs seconds for a second copy of what was already said.
        """
        xml = self.camera._post(
            "/onvif/media_service",
            f'<GetVideoEncoderConfigurationOptions xmlns="{MEDIA}">'
            f"<ConfigurationToken>{_xml(token)}</ConfigurationToken>"
            "</GetVideoEncoderConfigurationOptions>",
        )
        low, high = parse_bitrate_range(xml)
        return EncoderLimits(sizes=parse_options(xml), bitrate_min=low, bitrate_max=high)

    def options(self, token: str) -> list[tuple[int, int]]:
        return self.limits(token).sizes

    def apply(
        self,
        config: EncoderConfig,
        *,
        kbps: int | None = None,
        size: tuple[int, int] | None = None,
        fps: int | None = None,
    ) -> EncoderConfig:
        """Write one configuration back with only the named fields changed."""
        wanted = EncoderConfig(**config.as_dict())
        if kbps is not None:
            wanted.bitrate_kbps = int(kbps)
        if size is not None:
            wanted.width, wanted.height = int(size[0]), int(size[1])
        if fps is not None:
            wanted.fps = int(fps)
        self._write(wanted)
        return wanted

    def cap_bitrate(self, config: EncoderConfig, kbps: int) -> EncoderConfig:
        """Set one configuration's bitrate limit, changing nothing else.

        Every field is sent back as the camera reported it, with only the
        bitrate replaced. ONVIF's Set is a whole-object write: omitting a field
        does not mean "leave it alone", it means "set it to nothing", and a
        camera that accepts that will quietly lose its resolution or frame rate.
        """
        return self.apply(config, kbps=kbps)

    def _write(self, config: EncoderConfig) -> None:
        if not config.token:
            raise PtzError("this encoder configuration has no token")

        kbps = config.bitrate_kbps or 2000
        rate_control = (
            f"<tt:RateControl FrameRateLimit=\"{config.fps or 25}\" "
            f'EncodingInterval="1" BitrateLimit="{int(kbps)}"/>'
        )
        resolution = (
            f'<tt:Resolution><tt:Width>{config.width}</tt:Width>'
            f"<tt:Height>{config.height}</tt:Height></tt:Resolution>"
            if config.width and config.height
            else ""
        )
        quality = f"<tt:Quality>{config.quality if config.quality is not None else 5}</tt:Quality>"
        h264 = (
            f"<tt:H264><tt:GovLength>{config.gov_length or 25}</tt:GovLength>"
            "<tt:H264Profile>Main</tt:H264Profile></tt:H264>"
            if (config.encoding or "").upper().startswith("H264")
            else ""
        )

        body = (
            f'<SetVideoEncoderConfiguration xmlns="{MEDIA}" '
            'xmlns:tt="http://www.onvif.org/ver10/schema">'
            f'<Configuration token="{_xml(config.token)}">'
            f"<tt:Name>{_xml(config.name or config.token)}</tt:Name>"
            "<tt:UseCount>1</tt:UseCount>"
            f"<tt:Encoding>{_xml(config.encoding or 'H264')}</tt:Encoding>"
            f"{resolution}{quality}{h264}{rate_control}"
            "</Configuration><ForcePersistence>true</ForcePersistence>"
            "</SetVideoEncoderConfiguration>"
        )
        self.camera._post("/onvif/media_service", body)


def fit_to_link(configs: list[EncoderConfig], ceiling_kbps: int) -> dict[str, int]:
    """Decide a bitrate for each stream so that together they fit the link.

    Shares are weighted by pixel count, because a 4K stream genuinely needs more
    than a thermal one to look like anything - but the total is what the link
    can carry, and a margin is left because panning spends every bit the encoder
    is allowed and a link at exactly 100% delivers nothing on time.
    """
    usable = int(ceiling_kbps * 0.75)
    weights = {}
    for config in configs:
        pixels = (config.width or 640) * (config.height or 480)
        weights[config.token] = pixels
    total = sum(weights.values()) or 1
    # No stream below 256 kb/s: under that the picture stops being useful at all.
    return {
        token: max(256, int(usable * weight / total)) for token, weight in weights.items()
    }


def apply_budget(encoders: CameraEncoders, budget_kbps: int) -> dict:
    """Share a link budget between the streams, write it, and check it landed.

    Four steps, and the last two are the ones this project has got wrong before:

    * **share it.** `fit_to_link` weights by pixel count, because a 4K stream
      genuinely needs more than a thermal one to look like anything.
    * **clamp it to what the camera says it will take.** The permitted range is
      in `GetVideoEncoderConfigurationOptions` and was being discarded. A value
      outside it is refused.
    * **write only what has changed.** Each write interrupts the stream -
      go2rtc reconnects and the operator sees the picture blip - so a write of
      the value that is already there is a blip bought for nothing.
    * **read it back.** An HTTP 200 is not evidence: this camera answers 200
      with a SOAP fault, and twice in this project a 200 has been taken as proof
      that a setting landed. What the camera reports AFTERWARDS is the answer,
      and anything else is reported as refused rather than counted as applied.

    Never changes a resolution or a frame rate. ONVIF's Set is a whole-object
    write, so both are sent back - exactly as the camera reported them.
    """
    configs = encoders.read()
    if not configs:
        return {"ok": False, "error": "the camera reported no encoder settings"}

    targets = fit_to_link(configs, budget_kbps)
    wanted: dict[str, int] = {}
    for config in configs:
        target = targets.get(config.token)
        if target is None:
            continue
        try:
            limits = encoders.limits(config.token)
        except Exception:  # noqa: BLE001 - a camera that will not say its range
            logger.warning(
                "the camera would not say what bitrates %s accepts; using the "
                "target as it stands",
                config.token,
                exc_info=True,
            )
            limits = EncoderLimits(sizes=[], bitrate_min=None, bitrate_max=None)
        wanted[config.token] = clamp_bitrate(target, limits)

    changed: list[str] = []
    written: dict[str, int] = {}
    for config in configs:
        target = wanted.get(config.token)
        if target is None or config.bitrate_kbps == target:
            continue
        encoders.cap_bitrate(config, target)
        written[config.token] = target
        changed.append(
            f"{config.name or config.token}: "
            f"{config.bitrate_kbps or 'uncapped'} -> {target} kb/s"
        )

    if not written:
        return {"ok": True, "changed": [], "applied": {}, "refused": []}

    # One read for all of them, not one per stream: this crosses the radio link.
    after = {config.token: config for config in encoders.read()}
    applied: dict[str, int] = {}
    refused: list[str] = []
    for token, target in written.items():
        landed = after.get(token)
        if landed is None or landed.bitrate_kbps != target:
            refused.append(token)
            continue
        applied[token] = target
    return {"ok": True, "changed": changed, "applied": applied, "refused": refused}
