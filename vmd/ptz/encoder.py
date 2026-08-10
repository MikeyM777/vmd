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


class CameraEncoders:
    """The camera's encoder settings, read and capped."""

    def __init__(self, camera: OnvifPtz) -> None:
        self.camera = camera

    def read(self) -> list[EncoderConfig]:
        xml = self.camera._post(
            "/onvif/media_service", f'<GetVideoEncoderConfigurations xmlns="{MEDIA}"/>'
        )
        return parse_configurations(xml)

    def cap_bitrate(self, config: EncoderConfig, kbps: int) -> EncoderConfig:
        """Set one configuration's bitrate limit, changing nothing else.

        Every field is sent back as the camera reported it, with only the
        bitrate replaced. ONVIF's Set is a whole-object write: omitting a field
        does not mean "leave it alone", it means "set it to nothing", and a
        camera that accepts that will quietly lose its resolution or frame rate.
        """
        if not config.token:
            raise PtzError("this encoder configuration has no token")

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
        return EncoderConfig(**{**config.as_dict(), "bitrate_kbps": int(kbps)})


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
