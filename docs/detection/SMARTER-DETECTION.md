# Making the detection smart

*Written 2026-08-18, against a target machine of an i5-12600 mini PC with 16 GB
and no graphics card. Every timing in here was measured rather than looked up,
except where it says otherwise and names the source.*

---

## The short version

The architecture that fixes this **is already designed and measured in this
repository**, in `spike/motion_crop_detect.py`. It was spiked, it worked, and
only half of it was ever shipped. The half that shipped is the motion stage —
which is the half that produces the false alarms.

```
    frame
      -> background subtraction at quarter scale     (cheap, finds candidates)
      -> merge blobs, pad, crop at NATIVE resolution (small, keeps the pixels)
      -> a detector on the CROPS only                (one or two per frame)
      -> N-of-M confirmation on what the detector agreed with
      -> event
```

Today the third and fourth steps are missing, so **anything that moves and is
big enough is an alarm**. That is the whole of why it is crappy: nothing ever
looks at what moved.

The i5-12600 can run the missing half. It has an Intel UHD 770, and OpenVINO
runs a 320×320 detector on that in **15–20 ms** (published figures, several
independent Frigate deployments). At one or two crops per frame, a few frames a
second, that is a small fraction of the machine.

---

## Why the obvious approach does not work here

**"Just run YOLO on the picture."** The camera sends FHD. Every detector resizes
its input — 640×640 is the usual — so 1920 wide becomes 640 wide, a factor of
three. The code's own figure for a person at 700 m is about 13 pixels tall on
the thermal head; at FHD it is nearer 6 or 7. After the resize that person is
**two pixels**. There is nothing left to detect. This is not a model problem and
a better model does not fix it.

Measured here, `yolo11n` on CPU:

| Input | Time | Rate |
|---|---|---|
| 192×192 | 22 ms | 45/sec |
| 320×320 | 32 ms | 31/sec |
| 448×448 | 45 ms | 22/sec |
| 640×640 | 69 ms | 14/sec |

So full-frame detection is both the slowest option **and** the one that throws
away the pixels that matter.

## Why cropping does work

A 13-pixel person inside a 112-pixel crop, fed to the model at 320×320, arrives
as a **37-pixel** person. That is comfortably inside what a small model can see.
The crop is taken at native resolution and never downscaled before the model
gets it — that is the entire trick, and it is why `spike/motion_crop_detect.py`
recovered people that the full-frame run in `spike/detect_video.py` missed.

The published alternative is **SAHI** — slice the frame into overlapping
640×640 tiles and run the model on each. It works, and it is worse here: FHD is
six to eight tiles, so it costs six to eight inferences on **every frame,
including the ones where nothing is happening**. Motion-gating costs zero
inferences on a still scene and one or two when something moves. On a perimeter
that is empty 99% of the time, that is not a close call.

---

## What the machine can do

Measured on this development machine, at FHD:

| Stage | Cost | Rate |
|---|---|---|
| Background subtraction, full frame | 19 ms | 53/sec |
| Background subtraction at quarter scale | 1.1 ms | 900/sec |
| `yolo11n` on one 320×320 crop, **CPU** | 32 ms | 31/sec |

Published for the target machine — Intel UHD 770 with OpenVINO, from several
independent Frigate deployments:

| Stage | Cost |
|---|---|
| 320×320 detector on the iGPU | 15–20 ms |

Four streams, analysed at 5 frames a second, with an average of one crop per
analysed frame is **20 inferences a second**: about a third of the iGPU at
15 ms each, and the CPU left alone for decoding and recording. There is room.

**No Coral or other accelerator is needed.** The UHD 770 is the accelerator.

---

## What to build, in the order I would build it

### 1. Put the crop detector back, on the visible stream only

The pieces exist. `spike/motion_crop_detect.py` is the algorithm,
`spike/diagnose_misses.py` is the tool that says *why* a real person was missed —
it classifies every miss as MOTION-BLIND, CROP-STARVED, DETECTOR-BLIND or
BELOW-THRESHOLD, which are four different fixes. That tool is the reason this
can be tuned rather than guessed at.

The detector's verdict should **filter** events, not name them. He was right to
remove the naming: a confident wrong noun is worse than no noun. What is wanted
is the model as a *veto* — motion says "something there", the model says "and it
is person-shaped", and only then is it an alarm. Nothing is ever labelled on
screen.

### 2. Thermal is a separate problem and should be treated as one

COCO-trained models have never seen a thermal image. Three honest options:

- **Leave thermal on motion alone**, with better geometry rules (below). It is
  the sensor that actually finds a warm body at 700 m, and it has the fewest
  distractors.
- **Fine-tune on FLIR ADAS v2** — free, 26,000 annotated thermal frames from a
  640×512 Tau 2, which is the same class of sensor. This is the standard answer
  and it is a day of training on a machine with a graphics card, done once, and
  the weights then ship in the folder.
- Treat the thermal blob geometrically: aspect ratio, size against range,
  persistence. Cheaper than a model and it is most of the benefit.

### 3. The rules that cost nothing and are missing

These are not machine learning and they throw away most of what a treeline
produces:

- **Aspect ratio.** A person is taller than wide. A branch is not.
- **Size against range.** The size rule is in pixels today, so it means a
  different thing at every zoom level. Calibrate the view once and it becomes
  "ignore anything smaller than a person at that distance" — which is what
  SightLogix built an entire product on.
- **Speed and bearing.** Too fast is a vehicle; drift is weather.
- **Areas to watch**, not only areas to ignore.

### 4. A better tracker

The current tracker drops low-confidence observations. **ByteTrack** keeps them
and associates them anyway, which is precisely the case here: a 7-pixel target
flickers in and out, and a tracker that only accepts confident frames breaks one
person into four tracks and confirms none of them.

---

## What this will and will not do

**It will** cut false alarms hard. Everything that moves is currently an alarm;
after this, everything that moves *and looks like a person or a vehicle* is.

**It will not** increase the range. The motion stage still decides what is
looked at, so anything the motion stage cannot see is still invisible. If the
system is missing real people today, that is a motion-stage problem and
`spike/diagnose_misses.py` will say so — and the fix is sensitivity, blob area
and the background model, not a detector.

**It costs** a dependency that is already declared (`ultralytics`, the `detect`
extra) plus OpenVINO on the target machine, and it costs the risk that the iGPU
is busier than expected because it is also decoding four video streams. That
last one has to be measured on the mini PC and cannot be settled here.

---

## The one thing to check first, before any of this

The sensitivity presets set a **minimum blob height as a share of frame height**.
On a 1080-line picture:

| Sensitivity | Smallest blob accepted |
|---|---|
| low | 32 px tall |
| **normal (the default)** | **16 px tall** |
| high | 5.4 px tall |

If a person at 700 m is 6–13 px, **the default preset rejects them and says
nothing.** No detector helps a system whose motion stage is discarding the
target before anything looks at it.

The detector already counts every rejection by rule and publishes them.
Open `<recordings>\detection.json` and read:

```json
"rejected": { "ignore_mask": 0, "horizon": 0, "too_small": 4183, "too_large": 0 }
```

A large `too_small` beside a near-zero event count is that fault, and the fix is
one setting rather than a month of work.

---

## Sources

Measured here: `spike/bench.py`, and the benchmarks re-run for this document.
Published figures and techniques:

- Frigate deployment reports for OpenVINO on Intel UHD 770 (15–20 ms at 320×320)
- SAHI — *Slicing Aided Hyper Inference*, and the 2026 adaptive-slicing follow-up
- ByteTrack — *Multi-Object Tracking by Associating Every Detection Box*
- Teledyne FLIR ADAS v2 thermal dataset (26,000 frames, 15 classes)
- SightLogix, on geospatial size filtering for perimeter thermal
