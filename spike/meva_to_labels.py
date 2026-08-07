"""Convert MEVA KPF ground truth into the label format our scorer understands.

    uv run python spike/meva_to_labels.py footage/meva/<clip>.avi

Reads `<clip>.geom.yml` (per-frame boxes, `ts1` already in seconds) and
`<clip>.types.yml` (track id -> class), and writes `<clip>.avi.labels.json`
with the same shape spike/label_tool.py produces.

IMPORTANT, and the reason this script prints a warning: MEVA annotation is
activity-driven. Only actors taking part in one of the annotated activities are
labelled. Other people may walk through the frame unlabelled. That makes these
clips valid for measuring RECALL (did we catch the labelled people?) but NOT for
measuring false alarms, because an unlabelled real person would be scored as a
false alarm. The script reports the annotated fraction of the clip so the
limitation stays visible.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import cv2

GEOM = re.compile(
    r"geom:\s*\{\s*id1:\s*(\d+)\s*,\s*id0:\s*\d+\s*,\s*ts0:\s*([\d.]+)\s*,\s*ts1:\s*([\d.]+)\s*,"
    r"\s*g0:\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)"
)
TYPES = re.compile(r"types:\s*\{\s*id1:\s*(\d+)\s*,\s*cset3:\s*\{\s*(\w+)")

VEHICLE_WORDS = {"vehicle", "car", "truck", "bus", "motorcycle", "bike", "bicycle"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MEVA KPF -> labels.json")
    parser.add_argument("video", help="path to the .avi (annotations sit beside it)")
    parser.add_argument("--merge-gap", type=float, default=1.0,
                        help="join two appearances of the same track separated by less than this")
    parser.add_argument("--min-height", type=int, default=0,
                        help="ignore boxes shorter than this many pixels")
    return parser.parse_args()


def annotation_path(video: Path, kind: str) -> Path:
    # <clip>.avi -> <clip>.geom.yml
    return video.with_suffix("").with_suffix(f".{kind}.yml") if video.suffixes[:-1] else video.with_name(
        video.stem + f".{kind}.yml"
    )


def main() -> int:
    args = parse_args()
    video = Path(args.video)
    if not video.exists():
        print(f"video not found: {video}")
        return 1

    geom_path = video.with_name(video.stem + ".geom.yml")
    types_path = video.with_name(video.stem + ".types.yml")
    for path in (geom_path, types_path):
        if not path.exists():
            print(f"annotation not found: {path}")
            return 1

    classes: dict[int, str] = {}
    for line in types_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = TYPES.search(line)
        if match:
            word = match.group(2).lower()
            classes[int(match.group(1))] = "vehicle" if word in VEHICLE_WORDS else word

    tracks: dict[int, list[tuple[float, int]]] = {}
    for line in geom_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = GEOM.search(line)
        if not match:
            continue
        track_id = int(match.group(1))
        seconds = float(match.group(3))
        height = int(match.group(7)) - int(match.group(5))
        if height < args.min_height:
            continue
        tracks.setdefault(track_id, []).append((seconds, height))

    capture = cv2.VideoCapture(str(video))
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height_px = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    capture.release()
    duration = frames / fps if fps else 0.0

    spans = []
    heights = []
    for track_id, samples in tracks.items():
        label = classes.get(track_id, "unknown")
        if label == "unknown":
            continue
        samples.sort()
        heights.extend(h for _, h in samples)
        start = previous = samples[0][0]
        for seconds, _ in samples[1:]:
            if seconds - previous > args.merge_gap:
                spans.append({"label": label, "start": round(start, 2), "end": round(previous, 2)})
                start = seconds
            previous = seconds
        spans.append({"label": label, "start": round(start, 2), "end": round(previous, 2)})

    spans.sort(key=lambda s: (s["start"], s["label"]))
    payload = {
        "video": video.name,
        "fps": round(fps, 3),
        "duration": round(duration, 2),
        "frames": frames,
        "spans": spans,
        "source": "MEVA KPF ground truth (activity-driven; unlabelled people may appear)",
    }
    out_path = video.with_name(video.name + ".labels.json")
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    covered = sum(s["end"] - s["start"] for s in spans)
    by_class: dict[str, int] = {}
    for span in spans:
        by_class[span["label"]] = by_class.get(span["label"], 0) + 1

    print(f"{video.name}")
    print(f"  {width}x{height_px}, {fps:.2f} fps, {duration:.1f}s")
    print(f"  tracks: {len(tracks)}   spans: {len(spans)}  {by_class}")
    if heights:
        heights.sort()
        print(f"  labelled box height: min {heights[0]} px, median {heights[len(heights) // 2]} px, "
              f"max {heights[-1]} px")
    print(f"  labelled time: {covered:.1f}s of {duration:.1f}s ({100 * covered / max(duration, 1):.0f}%)")
    print(f"  -> {out_path.name}")
    if covered < 0.5 * duration:
        print("  WARNING: most of this clip is unannotated. Use it for RECALL only;")
        print("           false-alarm counts from it would be meaningless.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
