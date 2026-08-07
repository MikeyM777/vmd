"""Explain why a labelled span produced no alarm.

There are only four reasons a real person never raises an alarm, and they call
for completely different fixes:

  MOTION-BLIND    the motion gate never produced a blob there. Fix the motion
                  stage (sensitivity, blob area, background model), not the model.
  CROP-STARVED    blobs existed but were filtered out before becoming a crop.
  DETECTOR-BLIND  the crop reached the detector and it saw nothing at all, even
                  at a throwaway confidence floor. Fix the model or the crop size.
  BELOW-THRESHOLD the detector did see a person, but under the alarm threshold,
                  or not on enough consecutive frames to satisfy N-of-M.
                  Fix the thresholds - the cheapest fix there is.

This runs the normal pipeline over the whole clip (so the background model is
trained exactly as in a real run) but only reports on frames inside the missed
spans, and re-runs the detector at a very low confidence floor so near-misses
become visible instead of silently vanishing.

    uv run python spike/diagnose_misses.py footage/walk_3mbps.mp4 \
        footage/walk_3mbps.mp4.labels.json footage/alarm_3mbps_crop448.alarms.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

PERSON = "person"
VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle", "bicycle", "train"}
GREEN = (0, 255, 0)
YELLOW = (0, 220, 220)
CYAN = (255, 255, 0)
WHITE = (255, 255, 255)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose missed detections")
    parser.add_argument("video")
    parser.add_argument("labels", help="<video>.labels.json")
    parser.add_argument("alarms", nargs="?", default="", help="alarms json; omit to diagnose every span")
    parser.add_argument("--model", default="yolo11n.pt")
    parser.add_argument("--crop-imgsz", type=int, default=448)
    parser.add_argument("--alarm-conf", type=float, default=0.25, help="threshold the real run used")
    parser.add_argument("--floor-conf", type=float, default=0.05, help="throwaway floor, to expose near-misses")
    parser.add_argument("--stride", type=int, default=3)
    parser.add_argument("--scale", type=float, default=0.25)
    parser.add_argument("--var-threshold", type=float, default=10.0)
    parser.add_argument("--min-blob-area", type=int, default=6)
    parser.add_argument("--pad", type=float, default=1.8)
    parser.add_argument("--min-crop", type=int, default=112)
    parser.add_argument("--max-crops", type=int, default=10)
    parser.add_argument("--grace", type=float, default=1.5)
    parser.add_argument("--label", default=PERSON, help="which labelled class to diagnose")
    parser.add_argument("--clips", action="store_true", help="also write one annotated clip per missed span")
    return parser.parse_args()


def merge_boxes(boxes, gap):
    merged: list[list[int]] = []
    for x, y, w, h in boxes:
        box = [x, y, x + w, y + h]
        for existing in merged:
            if (box[0] - gap < existing[2] and existing[0] - gap < box[2]
                    and box[1] - gap < existing[3] and existing[1] - gap < box[3]):
                existing[0] = min(existing[0], box[0])
                existing[1] = min(existing[1], box[1])
                existing[2] = max(existing[2], box[2])
                existing[3] = max(existing[3], box[3])
                break
        else:
            merged.append(box)
    return [(b[0], b[1], b[2] - b[0], b[3] - b[1]) for b in merged]


def to_crop_rect(box, frame_w, frame_h, pad, min_side):
    x, y, w, h = box
    cx, cy = x + w // 2, y + h // 2
    side = min(max(int(max(w, h) * pad), min_side), frame_w, frame_h)
    half = side // 2
    return min(max(cx - half, 0), frame_w - side), min(max(cy - half, 0), frame_h - side), side


def main() -> int:
    args = parse_args()
    video = Path(args.video)
    labels_path = Path(args.labels)
    if not video.exists() or not labels_path.exists():
        print("video or labels file not found")
        return 1

    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    spans = [s for s in labels.get("spans", []) if s["label"] == args.label]

    if args.alarms:
        run = json.loads(Path(args.alarms).read_text(encoding="utf-8"))
        alarm_times = [
            float(a["at"]) for a in run.get("alarms", [])
            if (a["label"] if a["label"] not in VEHICLE_CLASSES else "vehicle") == args.label
        ]
        targets = [
            s for s in spans
            if not any(s["start"] - args.grace <= t <= s["end"] + args.grace for t in alarm_times)
        ]
        print(f"{len(targets)} missed span(s) of {len(spans)} labelled\n")
    else:
        targets = spans
        print(f"diagnosing all {len(targets)} labelled span(s)\n")

    if not targets:
        print("nothing to diagnose")
        return 0

    from ultralytics import YOLO

    model = YOLO(args.model)
    names = model.names

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        print("could not open video")
        return 1
    frame_w = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    out_fps = max(fps / args.stride, 1.0)

    subtractor = cv2.createBackgroundSubtractorMOG2(
        history=500, varThreshold=args.var_threshold, detectShadows=True
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    stats = {
        id(span): {
            "span": span,
            "frames": 0,
            "frames_with_motion": 0,
            "frames_with_crops": 0,
            "frames_with_any_detection": 0,
            "frames_above_alarm_conf": 0,
            "best_conf": 0.0,
            "confs": [],
            "blob_heights": [],
        }
        for span in targets
    }
    writers = {}
    if args.clips:
        for index, span in enumerate(targets):
            path = video.with_name(f"{video.stem}_miss{index + 1}_{span['start']:.0f}s.mp4")
            writers[id(span)] = (
                cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), out_fps, (frame_w, frame_h)),
                path,
            )

    source_index = -1
    while True:
        ok, image = capture.read()
        if not ok:
            break
        source_index += 1
        if source_index % args.stride:
            continue
        now = source_index / fps

        small = cv2.resize(image, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mask = subtractor.apply(gray)
        mask[mask < 255] = 0
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.dilate(mask, kernel, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        raw_boxes = []
        for contour in contours:
            if cv2.contourArea(contour) < args.min_blob_area:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            raw_boxes.append((int(x / args.scale), int(y / args.scale),
                              int(w / args.scale), int(h / args.scale)))

        inside = [s for s in targets if s["start"] <= now <= s["end"]]
        if not inside:
            continue

        boxes = merge_boxes(raw_boxes, gap=int(0.02 * frame_w))
        boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
        boxes = boxes[: args.max_crops]

        crops, rects = [], []
        for box in boxes:
            left, top, side = to_crop_rect(box, frame_w, frame_h, args.pad, args.min_crop)
            crops.append(image[top : top + side, left : left + side].copy())
            rects.append((left, top, side))

        detections = []
        if crops:
            results = model.predict(
                crops, imgsz=args.crop_imgsz, conf=args.floor_conf, verbose=False, device="cpu"
            )
            for result, (left, top, _side) in zip(results, rects):
                for det in result.boxes:
                    label = names[int(det.cls)]
                    normalised = "vehicle" if label in VEHICLE_CLASSES else label
                    if normalised != args.label:
                        continue
                    x1, y1, x2, y2 = (float(v) for v in det.xyxy[0])
                    detections.append(
                        (float(det.conf), (int(left + x1), int(top + y1), int(left + x2), int(top + y2)))
                    )

        for span in inside:
            entry = stats[id(span)]
            entry["frames"] += 1
            if raw_boxes:
                entry["frames_with_motion"] += 1
                entry["blob_heights"].append(max(h for _, _, _, h in raw_boxes))
            if crops:
                entry["frames_with_crops"] += 1
            if detections:
                entry["frames_with_any_detection"] += 1
                best = max(c for c, _ in detections)
                entry["best_conf"] = max(entry["best_conf"], best)
                entry["confs"].append(best)
                if best >= args.alarm_conf:
                    entry["frames_above_alarm_conf"] += 1

        if args.clips:
            annotated = image.copy()
            for x, y, w, h in raw_boxes:
                cv2.rectangle(annotated, (x, y), (x + w, y + h), YELLOW, 1)
            for left, top, side in rects:
                cv2.rectangle(annotated, (left, top), (left + side, top + side), CYAN, 1)
            for confidence, (x1, y1, x2, y2) in detections:
                colour = GREEN if confidence >= args.alarm_conf else (0, 165, 255)
                cv2.rectangle(annotated, (x1, y1), (x2, y2), colour, 3)
                cv2.putText(annotated, f"{args.label} {confidence:.2f}", (x1, max(y1 - 8, 18)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, colour, 2, cv2.LINE_AA)
            cv2.putText(annotated, f"t={now:.2f}s blobs={len(raw_boxes)} crops={len(crops)} det={len(detections)}",
                        (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, WHITE, 2, cv2.LINE_AA)
            for span in inside:
                writers[id(span)][0].write(annotated)

    capture.release()
    for writer, _ in writers.values():
        writer.release()

    print(f"{'span':>18} {'frm':>4} {'motion':>7} {'crops':>6} {'detect':>7} {'>=conf':>7} {'best':>5}  verdict")
    print("-" * 96)
    for span in targets:
        entry = stats[id(span)]
        frames = entry["frames"]
        if not frames:
            verdict = "NO FRAMES ANALYSED (span shorter than the analysis interval)"
        elif entry["frames_with_motion"] == 0:
            verdict = "MOTION-BLIND - gate never fired"
        elif entry["frames_with_crops"] == 0:
            verdict = "CROP-STARVED - blobs filtered out before becoming crops"
        elif entry["frames_with_any_detection"] == 0:
            verdict = f"DETECTOR-BLIND - nothing found even at conf {args.floor_conf}"
        elif entry["frames_above_alarm_conf"] == 0:
            verdict = f"BELOW-THRESHOLD - best {entry['best_conf']:.2f} < {args.alarm_conf}"
        else:
            verdict = (f"CONFIRMATION - {entry['frames_above_alarm_conf']}/{frames} frames above conf, "
                       "too few or too scattered for N-of-M")
        label = f"{span['start']:6.2f}-{span['end']:6.2f}"
        print(f"{label:>18} {frames:>4} {entry['frames_with_motion']:>7} {entry['frames_with_crops']:>6} "
              f"{entry['frames_with_any_detection']:>7} {entry['frames_above_alarm_conf']:>7} "
              f"{entry['best_conf']:>5.2f}  {verdict}")
        if entry["blob_heights"]:
            print(f"{'':>18} largest motion blob height: median "
                  f"{int(np.median(entry['blob_heights']))} px, max {max(entry['blob_heights'])} px")

    if args.clips:
        print("\nclips written:")
        for _, path in writers.values():
            print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
