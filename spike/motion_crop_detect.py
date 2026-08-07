"""Spike: motion-gated crop detection.

The real architecture in one file, so we can measure it before building it properly:

    frame -> MOG2 background subtraction (quarter scale)
          -> moving blobs (parked cars produce none)
          -> merge nearby blobs, expand to a padded crop
          -> run the detector ONLY on those crops, at native resolution
          -> map detections back to full-frame coordinates

Compare against spike/detect_video.py, which runs the detector on the whole
downscaled frame. The two questions this answers:
  1. Does cropping recover the small far-away person the full-frame run misses?
  2. How much compute does skipping the static background actually save?

Usage:
    uv run python spike/motion_crop_detect.py footage/clip.mov
    uv run python spike/motion_crop_detect.py footage/clip.mov --stride 3 --conf 0.25
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

PERSON = {"person"}
VEHICLE = {"car", "truck", "bus", "motorcycle", "bicycle", "train"}
WANTED = PERSON | VEHICLE

GREEN = (0, 255, 0)      # confirmed person
BLUE = (255, 160, 0)     # confirmed vehicle
YELLOW = (0, 220, 220)   # raw motion blob
CYAN = (255, 255, 0)     # crop actually sent to the detector
RED = (0, 0, 255)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Motion-gated crop detection spike")
    parser.add_argument("video")
    parser.add_argument("--model", default="yolo11n.pt")
    parser.add_argument("--crop-imgsz", type=int, default=320, help="detector input size for crops")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--stride", type=int, default=3, help="analyse every Nth frame")
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--scale", type=float, default=0.25, help="motion analysis scale")
    parser.add_argument("--min-blob-area", type=int, default=20, help="min blob area in downscaled px")
    parser.add_argument("--global-motion-frac", type=float, default=0.35)
    parser.add_argument("--pad", type=float, default=1.8, help="crop size as a multiple of the blob box")
    parser.add_argument("--min-crop", type=int, default=112, help="minimum crop side in full-res px")
    parser.add_argument("--max-crops", type=int, default=8, help="most crops to detect per frame")
    parser.add_argument("--warmup", type=int, default=15, help="analysed frames used to learn the background")
    parser.add_argument("--out", default="")
    return parser.parse_args()


def merge_boxes(boxes: list[tuple[int, int, int, int]], gap: int) -> list[tuple[int, int, int, int]]:
    """Union boxes that overlap once inflated by `gap`. Keeps the crop count sane."""
    merged: list[list[int]] = []
    for x, y, w, h in boxes:
        box = [x, y, x + w, y + h]
        absorbed = False
        for existing in merged:
            if (
                box[0] - gap < existing[2]
                and existing[0] - gap < box[2]
                and box[1] - gap < existing[3]
                and existing[1] - gap < box[3]
            ):
                existing[0] = min(existing[0], box[0])
                existing[1] = min(existing[1], box[1])
                existing[2] = max(existing[2], box[2])
                existing[3] = max(existing[3], box[3])
                absorbed = True
                break
        if not absorbed:
            merged.append(box)
    return [(b[0], b[1], b[2] - b[0], b[3] - b[1]) for b in merged]


def to_crop_rect(box, frame_w, frame_h, pad, min_side):
    """Expand a motion box into a padded square crop clamped to the frame."""
    x, y, w, h = box
    cx, cy = x + w // 2, y + h // 2
    side = int(max(w, h) * pad)
    side = max(side, min_side)
    side = min(side, frame_w, frame_h)
    half = side // 2
    left = min(max(cx - half, 0), frame_w - side)
    top = min(max(cy - half, 0), frame_h - side)
    return left, top, side, side


def main() -> int:
    args = parse_args()
    video = Path(args.video)
    if not video.exists():
        print(f"video not found: {video}")
        return 1

    from ultralytics import YOLO

    print(f"loading model {args.model} ...")
    model = YOLO(args.model)
    names = model.names

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        print(f"could not open video: {video}")
        return 1

    frame_w = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    src_fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"video: {frame_w}x{frame_h} @ {src_fps:.2f} fps, {total} frames")
    print(
        f"settings: scale={args.scale} crop_imgsz={args.crop_imgsz} conf={args.conf} "
        f"stride={args.stride} pad={args.pad}\n"
    )

    out_path = Path(args.out) if args.out else video.with_name(video.stem + "_motioncrop.mp4")
    writer = cv2.VideoWriter(
        str(out_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        max(src_fps / args.stride, 1.0),
        (frame_w, frame_h),
    )

    subtractor = cv2.createBackgroundSubtractorMOG2(
        history=500, varThreshold=16, detectShadows=True
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    processed = 0
    analysed = 0                 # frames past warmup that were actually detected on
    frames_with_person = 0
    frames_with_motion = 0
    frames_suppressed = 0
    crops_total = 0
    class_counts: Counter[str] = Counter()
    person_heights: list[int] = []
    best_conf = 0.0
    motion_ms: list[float] = []
    detect_ms: list[float] = []
    moving_fracs: list[float] = []
    source_index = -1

    while True:
        ok, image = capture.read()
        if not ok:
            break
        source_index += 1
        if source_index % args.stride:
            continue
        processed += 1

        # --- motion gate -------------------------------------------------
        started = time.perf_counter()
        small = cv2.resize(image, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mask = subtractor.apply(gray)
        mask[mask < 255] = 0                      # drop MOG2 shadow pixels (127)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.dilate(mask, kernel, iterations=2)
        moving_frac = float(np.count_nonzero(mask)) / mask.size
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        raw_boxes = []
        for contour in contours:
            if cv2.contourArea(contour) < args.min_blob_area:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            raw_boxes.append(
                (int(x / args.scale), int(y / args.scale), int(w / args.scale), int(h / args.scale))
            )
        motion_ms.append((time.perf_counter() - started) * 1000.0)
        moving_fracs.append(moving_frac)

        suppressed = moving_frac > args.global_motion_frac
        if suppressed:
            frames_suppressed += 1
            subtractor = cv2.createBackgroundSubtractorMOG2(
                history=500, varThreshold=16, detectShadows=True
            )
            raw_boxes = []

        warming = processed <= args.warmup
        boxes = [] if warming else merge_boxes(raw_boxes, gap=int(0.02 * frame_w))
        boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
        boxes = boxes[: args.max_crops]
        if boxes:
            frames_with_motion += 1

        # --- detect on crops ---------------------------------------------
        crops, rects = [], []
        for box in boxes:
            left, top, side, _ = to_crop_rect(box, frame_w, frame_h, args.pad, args.min_crop)
            crops.append(image[top : top + side, left : left + side].copy())
            rects.append((left, top, side))
        crops_total += len(crops)

        detections = []
        if crops:
            started = time.perf_counter()
            results = model.predict(
                crops, imgsz=args.crop_imgsz, conf=args.conf, verbose=False, device="cpu"
            )
            detect_ms.append((time.perf_counter() - started) * 1000.0)
            for result, (left, top, _side) in zip(results, rects):
                for det in result.boxes:
                    label = names[int(det.cls)]
                    if label not in WANTED:
                        continue
                    # Ultralytics rescales boxes to the crop's pixel coordinates,
                    # so only the crop offset has to be added back.
                    cx1, cy1, cx2, cy2 = (float(v) for v in det.xyxy[0])
                    detections.append(
                        (
                            label,
                            float(det.conf),
                            int(left + cx1),
                            int(top + cy1),
                            int(left + cx2),
                            int(top + cy2),
                        )
                    )
        else:
            detect_ms.append(0.0)

        if not warming:
            analysed += 1

        # --- draw ---------------------------------------------------------
        for x, y, w, h in raw_boxes:
            cv2.rectangle(image, (x, y), (x + w, y + h), YELLOW, 1)
        for left, top, side in rects:
            cv2.rectangle(image, (left, top), (left + side, top + side), CYAN, 1)

        found_person = False
        for label, confidence, x1, y1, x2, y2 in detections:
            class_counts[label] += 1
            colour = GREEN if label in PERSON else BLUE
            if label in PERSON:
                found_person = True
                best_conf = max(best_conf, confidence)
                person_heights.append(y2 - y1)
            cv2.rectangle(image, (x1, y1), (x2, y2), colour, 3)
            cv2.putText(
                image,
                f"{label} {confidence:.2f} h={y2 - y1}px",
                (x1, max(y1 - 8, 18)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                colour,
                2,
                cv2.LINE_AA,
            )
        if found_person:
            frames_with_person += 1

        banner = (
            f"f{source_index} motion={len(raw_boxes)} crops={len(crops)} "
            f"mov={moving_frac:.2f} {motion_ms[-1]:.0f}+{detect_ms[-1]:.0f}ms"
        )
        if warming:
            banner = "WARMUP  " + banner
        if suppressed:
            banner = "GLOBAL-MOTION SUPPRESSED  " + banner
        cv2.putText(
            image, banner, (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
            RED if suppressed else (255, 255, 255), 2, cv2.LINE_AA,
        )
        writer.write(image)

        if processed % 50 == 0:
            print(f"  {processed} frames, {frames_with_person} with a person, {crops_total} crops")
        if args.max_frames and processed >= args.max_frames:
            break

    capture.release()
    writer.release()

    if not analysed:
        print("no frames analysed")
        return 1

    def median(values):
        ordered = sorted(values)
        return ordered[len(ordered) // 2] if ordered else 0.0

    total_ms = [m + d for m, d in zip(motion_ms, detect_ms)]
    print("\n--- results (motion-gated crops) ---")
    print(f"frames analysed        : {analysed} (after {args.warmup} warmup frames)")
    print(f"frames with motion     : {frames_with_motion}")
    print(f"frames global-suppressed: {frames_suppressed}")
    print(f"frames with a person   : {frames_with_person} ({100 * frames_with_person / analysed:.0f}%)")
    print(f"best person confidence : {best_conf:.2f}")
    if person_heights:
        print(f"person box height      : {min(person_heights)}-{max(person_heights)} px (frame {frame_h} px tall)")
    print(f"detections by class    : {dict(class_counts) or 'none'}")
    print(f"crops per analysed frm : {crops_total / analysed:.1f}")
    print(f"moving fraction median : {median(moving_fracs):.3f}")
    print(f"motion gate median     : {median(motion_ms):.0f} ms")
    print(f"detector median        : {median(detect_ms):.0f} ms")
    print(f"total median           : {median(total_ms):.0f} ms  ({1000 / max(median(total_ms), 1):.1f} fps single stream)")
    print(f"\nannotated video        : {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
