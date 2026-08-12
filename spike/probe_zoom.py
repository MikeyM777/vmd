"""What the camera says about its lenses, and which one each zoom bar drives.

Run this on the console laptop, with the camera reachable:

    uv run python spike/probe_zoom.py

It reads nothing but the camera and `settings.json`, moves nothing, and prints
what it found. To test that a lens really responds, add its view's name:

    uv run python spike/probe_zoom.py --move thermal

which nudges that one lens in and back out again, reading the position at each
step, and says whether the camera reported any change.

Why this exists. The zoom bars are matched to the camera's media profiles by
`vmd/ptz/onvif.py:match_profiles`, from the profile names and the video source
tokens. That is guesswork about a vendor's naming, and when it guesses wrong
nothing reports an error: the camera accepts the command and carries it out on
the other lens, or on a profile that has no PTZ at all and quietly faults. What
the operator sees is a slider that does nothing, which looks exactly like a
command lost over the radio link.

So this prints the evidence rather than the conclusion: every profile, what it
is called, which video source it is on, whether it has a PTZ configuration at
all, and where its zoom is now. Send the whole output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vmd.ptz.onvif import (  # noqa: E402
    MEDIA,
    OnvifPtz,
    PtzError,
    match_profiles,
    read_profiles,
)
from vmd.settings import load_settings  # noqa: E402


def find_settings() -> Path:
    """Wherever the console keeps it, from wherever this was run."""
    here = Path(__file__).resolve().parent.parent
    for candidate in (Path("settings.json"), here / "settings.json"):
        if candidate.exists():
            return candidate
    raise SystemExit(
        "Could not find settings.json. Run this from the folder VMD is installed in."
    )


def has_ptz(block: str) -> bool:
    """Whether a profile carries a PTZ configuration.

    A profile without one cannot be zoomed at all: the camera answers an
    AbsoluteMove against it with a fault. It is the single most useful fact in
    the whole answer and the console was not reading it.
    """
    return "PTZConfiguration" in block


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--move",
        metavar="VIEW",
        help="nudge this view's lens and report whether the camera moved",
    )
    args = parser.parse_args()

    path = find_settings()
    settings = load_settings(path)
    camera = settings.camera
    if not camera.host.strip():
        raise SystemExit("No camera address in settings.json.")

    print(f"settings      : {path}")
    print(f"camera        : {camera.host}")
    print("views in settings, in the order the console lists them:")
    for stream in camera.streams:
        heat = "  (ticked as the heat camera)" if stream.thermal else ""
        print(f"    {stream.name:<12} {stream.url}{heat}")
    print()

    ptz = OnvifPtz(camera.host, camera.username, camera.password)
    capability = ptz.connect()
    print(f"PTZ available : {capability.available}   ({capability.reason})")
    print(f"absolute zoom : {capability.absolute_zoom}")
    print(f"auth accepted : {capability.auth or 'unknown'}")
    print()

    try:
        raw = ptz._post("/onvif/media_service", f'<GetProfiles xmlns="{MEDIA}"/>')
    except PtzError as exc:
        raise SystemExit(f"The camera would not list its profiles: {exc}") from exc

    profiles = read_profiles(raw)
    import re

    blocks = re.findall(
        r"<(?:\w+:)?Profiles\b(.*?)</(?:\w+:)?Profiles>", raw, re.DOTALL
    )
    ptz_by_token: dict[str, bool] = {}
    for block, profile in zip(blocks, profiles):
        ptz_by_token[profile.token] = has_ptz(block)

    print(f"the camera lists {len(profiles)} media profile(s):")
    print(f"    {'token':<22} {'name':<22} {'video source':<20} can zoom?")
    for profile in profiles:
        can = ptz_by_token.get(profile.token)
        mark = "yes" if can else ("NO - no PTZ config" if can is False else "unknown")
        print(
            f"    {profile.token:<22} {profile.name:<22} "
            f"{profile.source or '(none reported)':<20} {mark}"
        )
    print()

    names = [stream.name for stream in camera.streams]
    chosen = match_profiles(names, profiles)
    print("what the console does with that today:")
    for stream in camera.streams:
        token = chosen.get(stream.name)
        if token is None:
            print(f"    {stream.name:<12} -> NOTHING (its zoom bar cannot work)")
            continue
        can = ptz_by_token.get(token)
        warn = "" if can is not False else "   <-- this profile has no PTZ config"
        print(f"    {stream.name:<12} -> {token}{warn}")
    if len(set(chosen.values())) == 1 and len(chosen) > 1:
        print("    ** both views point at the SAME profile: one bar moves the other's picture **")
    print()

    print("where each lens says it is now:")
    for profile in profiles:
        try:
            position = ptz.position(profile=profile.token)
        except PtzError as exc:
            print(f"    {profile.token:<22} refused: {exc}")
            continue
        if not position:
            print(f"    {profile.token:<22} reports no position at all")
            continue
        zoom = position.get("zoom")
        print(
            f"    {profile.token:<22} zoom={zoom!r}  pan={position.get('pan')!r} "
            f"tilt={position.get('tilt')!r}"
        )
    print()

    if not args.move:
        print("Nothing was moved. To test one lens for real:")
        print(f"    uv run python spike/probe_zoom.py --move {names[0] if names else 'thermal'}")
        return 0

    token = chosen.get(args.move)
    if token is None:
        raise SystemExit(f"There is no view called {args.move!r} with a profile behind it.")

    print(f"--- moving {args.move} (profile {token}) ---")

    def read() -> float | None:
        try:
            answer = ptz.position(profile=token) or {}
        except PtzError as exc:
            print(f"    could not read the position: {exc}")
            return None
        return answer.get("zoom")

    before = read()
    print(f"    zoom before      : {before!r}")

    target = 0.5 if before is None else min(1.0, (before or 0.0) + 0.3)
    try:
        ptz.zoom_to(target, profile=token)
        print(f"    sent AbsoluteMove: zoom -> {target:.2f}   (accepted)")
    except PtzError as exc:
        print(f"    AbsoluteMove REFUSED: {exc}")
        print("    trying a held zoom instead, the way the buttons do it...")
        try:
            ptz.move(0.0, 0.0, 0.5, profile=token)
            import time

            time.sleep(1.5)
            ptz.stop(profile=token, pan_tilt=False, zoom=True)
            print("    continuous zoom accepted")
        except PtzError as second:
            print(f"    continuous zoom REFUSED too: {second}")
            print()
            print("    VERDICT: this profile will not zoom. That is the fault.")
            return 1

    import time

    time.sleep(2.5)
    after = read()
    print(f"    zoom after       : {after!r}")
    print()
    if before is None or after is None:
        print("    The camera does not report a zoom position, so this cannot say")
        print("    whether the lens moved. Watch the picture and say what happened.")
    elif abs((after or 0) - (before or 0)) < 0.01:
        print("    VERDICT: the camera accepted the command and did not move.")
        print("    Either this profile drives the other lens, or that lens is fixed.")
    else:
        print("    VERDICT: this lens moved. The command reached the right place.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
