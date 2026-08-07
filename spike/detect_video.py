"""Spike: run a small YOLO model over a video and report what it finds.

Not production code. Throwaway experiment to answer three questions:
  1. Does an off-the-shelf detector see the person in this footage at all?
  2. How confident is it, and how big is the person in pixels?
  3. How fast does it run on this laptop's CPU?

Usage:
    uv run python spike/detect_video.py "C:\\path\\to\\clip.MOV"
    uv run python spike/detect_video.py clip.MOV --imgsz 1280 --conf 0.15 --stride 3
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from pathlib import Path

import cv2

# COCO classes we care about for this project.
PERSON = {"person"}
VEHICLE = {"car", "truck", "bus", "motorcycle", "bicycle", "train"}
WANTED = PERSON | VEHICLE

GREEN = (0, 255, 0)
BLUE = (255, 160, 0)
GREY = (140, 140, 140)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a small YOLO model over a video")
    parser.add_argument("video", help="path to the video file")
    parser.add_argument("--model", default="yolo11n.pt", help="ultralytics model name or path")
    parser.add_argument("--imgsz", type=int, default=960, help="detector input size")
    parser.add_argument("--conf", type=float, default=0.20, help="confidence threshold")
    parser.add_argument("--stride", type=int, default=2, help="process every Nth frame")
    parser.add_argument("--max-frames", type=int, default=0, help="stop after N processed frames")
    parser.add_argument("--out", default="", help="annotated output path (default: <video>_det.mp4)")
    parser.add_argument("--all-classes", action="store_true", help="draw every COCO class, not just person/vehicle")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    video = Path(args.video)
    if not video.exists():
        print(f"video not found: {video}")
        return 1

    from ultralytics import YOLO  # imported late so --help stays fast

    print(f"loading model {args.model} ...")
    model = YOLO(args.model)
    names = model.names

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        print(f"could not open video: {video}")
        return 1

    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    src_fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"video: {width}x{height} @ {src_fps:.2f} fps, {total} frames")
    print(f"settings: imgsz={args.imgsz} conf={args.conf} stride={args.stride}\n")

    out_path = Path(args.out) if args.out else video.with_name(video.stem + "_det.mp4")
    writer = cv2.VideoWriter(
        str(out_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        max(src_fps / args.stride, 1.0),
        (width, height),
    )

    class_counts: Counter[str] = Counter()
    frames_with_person = 0
    processed = 0
    smallest_person = None
    largest_person = None
    best_conf = 0.0
    inference_ms: list[float] = []
    source_index = -1

    while True:
        ok, image = capture.read()
        if not ok:
            break
        source_index += 1
        if source_index % args.stride:
            continue

        started = time.perf_counter()
        result = model.predict(
            image, imgsz=args.imgsz, conf=args.conf, verbose=False, device="cpu"
        )[0]
        inference_ms.append((time.perf_counter() - started) * 1000.0)
        processed += 1

        found_person = False
        for box in result.boxes:
            label = names[int(box.cls)]
            confidence = float(box.conf)
            if not args.all_classes and label not in WANTED:
                continue
            class_counts[label] += 1
            x1, y1, x2, y2 = (int(v) for v in box.xyxy[0])
            box_height = y2 - y1

            if label in PERSON:
                found_person = True
                best_conf = max(best_conf, confidence)
                smallest_person = box_height if smallest_person is None else min(smallest_person, box_height)
                largest_person = box_height if largest_person is None else max(largest_person, box_height)
                colour = GREEN
            elif label in VEHICLE:
                colour = BLUE
            else:
                colour = GREY

            cv2.rectangle(image, (x1, y1), (x2, y2), colour, 2)
            cv2.putText(
                image,
                f"{label} {confidence:.2f} h={box_height}px",
                (x1, max(y1 - 6, 14)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                colour,
                2,
                cv2.LINE_AA,
            )

        if found_person:
            frames_with_person += 1

        cv2.putText(
            image,
            f"frame {source_index}  {inference_ms[-1]:.0f} ms",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        writer.write(image)

        if processed % 20 == 0:
            print(f"  {processed} frames processed, {frames_with_person} with a person")
        if args.max_frames and processed >= args.max_frames:
            break

    capture.release()
    writer.release()

    if not processed:
        print("no frames processed")
        return 1

    inference_ms.sort()
    median = inference_ms[len(inference_ms) // 2]
    p95 = inference_ms[int(len(inference_ms) * 0.95) - 1]

    print("\n--- results ---")
    print(f"frames processed      : {processed}")
    print(f"frames with a person  : {frames_with_person} ({100 * frames_with_person / processed:.0f}%)")
    print(f"best person confidence: {best_conf:.2f}")
    if smallest_person is not None:
        print(f"person box height     : {smallest_person}-{largest_person} px (frame is {height} px tall)")
    print(f"detections by class   : {dict(class_counts) or 'none'}")
    print(f"inference median      : {median:.0f} ms  ({1000 / median:.1f} fps single stream)")
    print(f"inference p95         : {p95:.0f} ms")
    print(f"\nannotated video       : {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
