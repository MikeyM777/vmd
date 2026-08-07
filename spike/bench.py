"""Measure what this machine can actually keep up with.

Run it on every machine you might deploy on. It answers one question:
how many times per second can this computer check a camera, and therefore
how many cameras can it watch at once?

    uv run python spike/bench.py footage/walk_3mbps.mp4

The pipeline is cheap when nothing moves and expensive when something does,
so a single average would be misleading. This reports both, then works out
the sustainable check rate for the realistic case: some fraction of the time
there is something in view.
"""

from __future__ import annotations

import argparse
import platform
import time
from pathlib import Path

import cv2
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark the VMD pipeline on this machine")
    parser.add_argument("video")
    parser.add_argument("--model", default="yolo11n.pt")
    parser.add_argument("--crop-imgsz", type=int, default=448)
    parser.add_argument("--conf", type=float, default=0.15)
    parser.add_argument("--scale", type=float, default=0.25)
    parser.add_argument("--frames", type=int, default=300, help="frames to time")
    parser.add_argument("--warmup", type=int, default=30, help="frames to discard first")
    parser.add_argument(
        "--busy",
        type=float,
        default=0.3,
        help="fraction of the time you expect something to be moving",
    )
    parser.add_argument("--cameras", type=int, default=4, help="cameras to report for")
    return parser.parse_args()


def describe_machine() -> str:
    name = platform.processor() or platform.machine()
    try:
        import subprocess

        if platform.system() == "Windows":
            output = subprocess.run(
                ["wmic", "cpu", "get", "name"], capture_output=True, text=True, timeout=10
            ).stdout
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            if len(lines) > 1:
                name = lines[1]
    except Exception:
        pass
    return name


def main() -> int:
    args = parse_args()
    video = Path(args.video)
    if not video.exists():
        print(f"video not found: {video}")
        return 1

    from ultralytics import YOLO

    print(f"machine    : {describe_machine()}")
    print(f"python     : {platform.python_version()} on {platform.system()} {platform.release()}")
    model = YOLO(args.model)
    try:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda":
            print(f"gpu        : {torch.cuda.get_device_name(0)}")
        else:
            print("gpu        : none available to PyTorch, running on CPU")
    except Exception:
        device = "cpu"
        print("gpu        : could not query, assuming CPU")

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        print("could not open video")
        return 1
    frame_w = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"video      : {frame_w}x{frame_h}")
    print(f"settings   : crop_imgsz={args.crop_imgsz} conf={args.conf} device={device}\n")

    subtractor = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=10, detectShadows=True)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    decode_ms: list[float] = []
    motion_ms: list[float] = []
    quiet_ms: list[float] = []   # total cost of a frame with no motion
    busy_ms: list[float] = []    # total cost of a frame that ran the detector
    processed = 0

    while processed < args.frames + args.warmup:
        started = time.perf_counter()
        ok, image = capture.read()
        decode = (time.perf_counter() - started) * 1000.0
        if not ok:
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue

        started = time.perf_counter()
        small = cv2.resize(image, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mask = subtractor.apply(gray)
        mask[mask < 255] = 0
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.dilate(mask, kernel, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = [cv2.boundingRect(c) for c in contours if cv2.contourArea(c) >= 6]
        motion = (time.perf_counter() - started) * 1000.0

        detect = 0.0
        if boxes:
            x, y, w, h = max(boxes, key=lambda b: b[2] * b[3])
            side = min(max(int(max(w, h) / args.scale * 1.8), 112), frame_w, frame_h)
            cx = int((x + w / 2) / args.scale)
            cy = int((y + h / 2) / args.scale)
            left = min(max(cx - side // 2, 0), frame_w - side)
            top = min(max(cy - side // 2, 0), frame_h - side)
            crop = image[top : top + side, left : left + side]
            started = time.perf_counter()
            model.predict(crop, imgsz=args.crop_imgsz, conf=args.conf, verbose=False, device=device)
            detect = (time.perf_counter() - started) * 1000.0

        processed += 1
        if processed <= args.warmup:
            continue

        decode_ms.append(decode)
        motion_ms.append(motion)
        if detect:
            busy_ms.append(decode + motion + detect)
        else:
            quiet_ms.append(decode + motion)

    capture.release()

    def median(values):
        return float(np.median(values)) if values else 0.0

    quiet = median(quiet_ms) or (median(decode_ms) + median(motion_ms))
    busy = median(busy_ms) or quiet
    blended = quiet * (1 - args.busy) + busy * args.busy

    print(f"frames timed        : {len(decode_ms)}")
    print(f"  decode            : {median(decode_ms):6.1f} ms")
    print(f"  motion gate       : {median(motion_ms):6.1f} ms")
    print(f"  QUIET frame total : {quiet:6.1f} ms   ({len(quiet_ms)} frames, nothing moving)")
    print(f"  BUSY frame total  : {busy:6.1f} ms   ({len(busy_ms)} frames, detector ran)")
    print(f"  blended @ {args.busy:.0%} busy : {blended:6.1f} ms\n")

    print(f"{'cameras':>8} {'checks/sec each':>16} {'gap between checks':>20}  verdict")
    print("-" * 74)
    for cameras in range(1, args.cameras + 1):
        rate = 1000.0 / (blended * cameras)
        gap = 1.0 / rate if rate else float("inf")
        if rate >= 8:
            verdict = "comfortable - catches brief events"
            if cameras == 1:
                verdict = "comfortable"
        elif rate >= 4:
            verdict = "fine - may miss events under ~0.5s"
        elif rate >= 2:
            verdict = "usable - misses events under ~1.5s"
        else:
            verdict = "too slow - only catches people who linger"
        print(f"{cameras:>8} {rate:>16.1f} {gap:>19.2f}s  {verdict}")

    print(
        "\nNote: the system drops frames rather than queueing them, so it never lags behind\n"
        "live video. A slower machine checks less often; it does not fall further behind."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
