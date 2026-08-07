"""Grade an alarm run against hand-made ground truth.

    uv run python spike/score.py footage/walk_3mbps.mp4.labels.json footage/alarm_3mbps_v3.alarms.json

Definitions used here, chosen to match how an operator judges a VMD system:

  detection   a labelled span counts as DETECTED if at least one alarm of a
              matching class falls inside it (plus a small grace window, since
              an alarm needs a few frames of confirmation before it fires).
  miss        a labelled span with no matching alarm inside it.
  false alarm an alarm that falls inside no labelled span of its class.
              Extra alarms inside a span that is already detected are not
              false alarms; they are counted separately as repeats.

Latency is measured from the start of the span to its first matching alarm,
which is what "how long before it tells me" actually means.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PERSON = "person"
VEHICLE = "vehicle"
VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle", "bicycle", "train", VEHICLE}


def normalise(label: str) -> str:
    return VEHICLE if label in VEHICLE_CLASSES else label


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score alarms against ground-truth spans")
    parser.add_argument("labels", help="<video>.labels.json from spike/label_tool.py")
    parser.add_argument("alarms", help="<run>.alarms.json from spike/alarm_demo.py")
    parser.add_argument(
        "--grace",
        type=float,
        default=1.5,
        help="seconds an alarm may fall past the end of a span and still count",
    )
    parser.add_argument(
        "--classes",
        default="person,vehicle",
        help="comma-separated classes to score",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    labels_path, alarms_path = Path(args.labels), Path(args.alarms)
    for path in (labels_path, alarms_path):
        if not path.exists():
            print(f"file not found: {path}")
            return 1

    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    run = json.loads(alarms_path.read_text(encoding="utf-8"))
    wanted = {c.strip() for c in args.classes.split(",") if c.strip()}

    spans = [
        {"label": normalise(s["label"]), "start": float(s["start"]), "end": float(s["end"])}
        for s in labels.get("spans", [])
    ]
    spans = [s for s in spans if s["label"] in wanted]
    alarms = [
        {"label": normalise(a["label"]), "at": float(a["at"])} for a in run.get("alarms", [])
    ]
    alarms = [a for a in alarms if a["label"] in wanted]

    duration = float(labels.get("duration") or run.get("duration") or 0.0)
    if not duration:
        print("no duration in either file; cannot compute rates")
        return 1

    labelled_time = sum(s["end"] - s["start"] for s in spans)

    for span in spans:
        # Grace applies on both ends: an alarm may fire slightly before the span
        # starts (the labeller's start time is a human judgement, and the system
        # may spot movement a frame earlier) or slightly after it ends (the
        # N-of-M rule needs a few frames to confirm).
        span["hits"] = [
            a
            for a in alarms
            if a["label"] == span["label"]
            and span["start"] - args.grace <= a["at"] <= span["end"] + args.grace
        ]
    matched = {id(a) for span in spans for a in span["hits"]}

    detected = [s for s in spans if s["hits"]]
    missed = [s for s in spans if not s["hits"]]
    false_alarms = [a for a in alarms if id(a) not in matched]
    repeats = sum(max(len(s["hits"]) - 1, 0) for s in spans)

    latencies = [min(h["at"] for h in s["hits"]) - s["start"] for s in detected]

    print(f"video           : {labels.get('video', '?')}")
    print(f"duration        : {duration:.1f}s   labelled activity: {labelled_time:.1f}s "
          f"({100 * labelled_time / duration:.0f}% of the clip)")
    print(f"classes scored  : {', '.join(sorted(wanted))}")
    print()
    print(f"labelled spans  : {len(spans)}")
    print(f"detected        : {len(detected)}")
    print(f"missed          : {len(missed)}")
    if spans:
        print(f"RECALL          : {100 * len(detected) / len(spans):.0f}%")
    if latencies:
        latencies.sort()
        print(f"latency         : median {latencies[len(latencies) // 2]:.1f}s, worst {latencies[-1]:.1f}s")
    print()
    print(f"alarms fired    : {len(alarms)}")
    print(f"FALSE ALARMS    : {len(false_alarms)}")
    print(f"  per hour      : {len(false_alarms) * 3600 / duration:.1f}")
    print(f"  per day       : {len(false_alarms) * 86400 / duration:.0f}")
    print(f"repeat alarms   : {repeats} (extra alarms inside an already-detected span)")

    if missed:
        print("\nmissed spans:")
        for span in missed:
            print(f"  {span['label']:8s} {span['start']:7.2f} -> {span['end']:7.2f}")
    if false_alarms:
        print("\nfalse alarms:")
        for alarm in false_alarms:
            print(f"  {alarm['label']:8s} at {alarm['at']:7.2f}s")

    print("\nsettings used for this run:")
    for key, value in (run.get("settings") or {}).items():
        print(f"  {key:20s} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
