"""Work out which FLIR Elara DX model you actually have.

The thermal lens is fixed and differs per model, and it decides everything at 700 m:
how much ground one position covers, and whether a person is 2 px or 13 px in thermal.
The datasheet lists eight variants; this finds out which one is on the pole.

Two ways, depending on what you can reach.

    # 1. Ask the camera, if it is on the network
    uv run python spike/identify_camera.py probe 192.168.1.64 --user admin --password secret

    # 2. Work it out from a picture, if it is not
    uv run python spike/identify_camera.py fov --pixels 240 --width 1.8 --distance 50 \
        --image-height 480 --sensor thermal

For the second one: point the thermal camera at something whose real size you know
(a person, a car, a door), photograph it, measure how many pixels tall or wide it is
in that image, and give the real distance. That is enough to recover the lens angle,
which identifies the model. No ladder required.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import urllib.request
from urllib.error import HTTPError, URLError

# Datasheet, Elara DX-Series. Thermal is a FIXED lens - this is what we are identifying.
MODELS = [
    # model,    hfov, vfov, thermal array,  focal
    ("DX-350", 50.0, 38.0, (640, 480), "4.3 mm"),
    ("DX-324", 24.0, 18.0, (640, 480), "9.1 mm"),
    ("DX-312", 12.0, 9.0, (640, 480), "18 mm"),
    ("DX-306", 6.0, 5.0, (640, 480), "36 mm"),
    ("DX-650", 50.0, 38.0, (640, 512), "8.7 mm"),
    ("DX-624", 24.0, 18.0, (640, 512), "18 mm"),
    ("DX-612", 12.0, 9.0, (640, 512), "36 mm"),
    ("DX-608", 8.0, 6.0, (640, 512), "55 mm"),
]

PERSON_HEIGHT_M = 1.8


def person_pixels(vfov_deg: float, sensor_rows: int, distance_m: float) -> float:
    """How tall a person appears, in pixels, at a given distance."""
    angle = math.degrees(math.atan(PERSON_HEIGHT_M / distance_m))
    return angle * (sensor_rows / vfov_deg)


def ground_width_m(hfov_deg: float, distance_m: float) -> float:
    """How wide a strip of ground one fixed position covers at a given distance."""
    return 2.0 * distance_m * math.tan(math.radians(hfov_deg / 2.0))


def report_table(distance_m: float, highlight: str | None = None) -> None:
    print(f"\n{'model':<9}{'thermal FOV':>13}{'array':>11}{'person':>9}{'ground':>10}   verdict")
    print("-" * 74)
    for name, hfov, vfov, (cols, rows), _focal in MODELS:
        px = person_pixels(vfov, rows, distance_m)
        width = ground_width_m(hfov, distance_m)
        if px >= 10:
            verdict = "detectable"
        elif px >= 5:
            verdict = "marginal"
        else:
            verdict = "too small"
        mark = " <-- yours" if highlight and name == highlight else ""
        print(
            f"{name:<9}{f'{hfov:.0f} x {vfov:.0f} deg':>13}{f'{cols}x{rows}':>11}"
            f"{px:>7.0f}px{width:>9.0f}m   {verdict}{mark}"
        )
    print(
        f"\nPerson height assumed {PERSON_HEIGHT_M} m at {distance_m:.0f} m."
        "\n'ground' is how wide a strip one fixed position sees - narrow lenses see"
        "\nfurther detail but cover less, so the head must patrol more."
    )


# ---------------------------------------------------------------- network probe

PROBE_PATHS = [
    "/cgi-bin/sysinfo.cgi",
    "/api/sysinfo",
    "/cgi-bin/nexus.cgi?action=getinfo",
    "/onvif/device_service",
    "/",
]
MODEL_PATTERN = re.compile(r"DX-?(\d{3})", re.IGNORECASE)


def probe(host: str, user: str, password: str, timeout: float) -> int:
    """Ask the camera what it is, over HTTP."""
    opener = urllib.request.build_opener()
    if user:
        manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, f"http://{host}/", user, password)
        opener = urllib.request.build_opener(
            urllib.request.HTTPDigestAuthHandler(manager),
            urllib.request.HTTPBasicAuthHandler(manager),
        )

    print(f"probing {host} ...\n")
    found = set()
    for path in PROBE_PATHS:
        url = f"http://{host}{path}"
        try:
            with opener.open(url, timeout=timeout) as response:
                body = response.read(20000).decode("utf-8", errors="replace")
            status = response.status
        except HTTPError as exc:
            print(f"  {path:<40} HTTP {exc.code}")
            continue
        except (URLError, OSError, TimeoutError) as exc:
            print(f"  {path:<40} unreachable ({exc})")
            continue

        matches = MODEL_PATTERN.findall(body)
        print(f"  {path:<40} HTTP {status}, {len(body)} bytes"
              f"{', model hints: ' + ', '.join(sorted(set(matches))) if matches else ''}")
        found.update(matches)

    if not found:
        print(
            "\nNo model string found. That does not mean the wrong camera - many units"
            "\nneed authentication before they reveal anything. Try again with"
            "\n--user/--password, or open http://%s in a browser and read the model"
            "\nfrom the status page." % host
        )
        return 1

    candidates = sorted({f"DX-{n}" for n in found})
    print(f"\nmodel(s) reported: {', '.join(candidates)}")
    report_table(700.0, highlight=candidates[0] if len(candidates) == 1 else None)
    return 0


# ---------------------------------------------------------------- fov from image


def from_measurement(
    pixels: float, real_size_m: float, distance_m: float, image_rows: int, sensor: str
) -> int:
    """Recover the lens angle from one measured object, and name the closest model."""
    angle_deg = math.degrees(math.atan(real_size_m / distance_m))
    vfov = angle_deg * (image_rows / pixels)
    print(f"\nobject subtends {angle_deg:.3f} deg and covers {pixels:.0f} of "
          f"{image_rows} rows")
    print(f"=> vertical field of view is about {vfov:.1f} deg")

    if sensor == "visible":
        print(
            "\nThat is the VISIBLE camera, whose lens zooms from 36.65 to 1.2 deg"
            "\nvertically. It tells you the current zoom, not the model - only the"
            "\nfixed thermal lens identifies the model. Re-measure on the thermal"
            "\nstream with --sensor thermal."
        )
        return 0

    ranked = sorted(MODELS, key=lambda m: abs(m[2] - vfov))
    best, second = ranked[0], ranked[1]
    print(f"\nclosest match : {best[0]}  (thermal {best[1]:.0f} x {best[2]:.0f} deg, "
          f"{best[4]} lens)")
    print(f"next closest  : {second[0]} (thermal {second[1]:.0f} x {second[2]:.0f} deg)")

    gap = abs(best[2] - vfov)
    if gap > 0.25 * best[2]:
        print(
            "\nThe measurement is not close to any listed lens. Check the distance and"
            "\nthe real size - a 10% error in either moves the answer by a whole model."
        )
    report_table(700.0, highlight=best[0])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Identify which Elara DX model you have")
    sub = parser.add_subparsers(dest="mode", required=True)

    p = sub.add_parser("probe", help="ask the camera over the network")
    p.add_argument("host")
    p.add_argument("--user", default="")
    p.add_argument("--password", default="")
    p.add_argument("--timeout", type=float, default=5.0)

    f = sub.add_parser("fov", help="work it out from a measured object in an image")
    f.add_argument("--pixels", type=float, required=True, help="object size in pixels")
    f.add_argument("--width", type=float, required=True, help="its real size in metres")
    f.add_argument("--distance", type=float, required=True, help="its distance in metres")
    f.add_argument("--image-height", type=int, required=True, help="image height in pixels")
    f.add_argument("--sensor", choices=["thermal", "visible"], default="thermal")

    t = sub.add_parser("table", help="just show what each model gives at a distance")
    t.add_argument("--distance", type=float, default=700.0)

    args = parser.parse_args()
    if args.mode == "probe":
        return probe(args.host, args.user, args.password, args.timeout)
    if args.mode == "fov":
        return from_measurement(
            args.pixels, args.width, args.distance, args.image_height, args.sensor
        )
    report_table(args.distance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
