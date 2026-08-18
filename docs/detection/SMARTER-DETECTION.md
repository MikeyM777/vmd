# Making the detection smart

*Written 2026-08-18, against a target machine of an i5-12600 mini PC with 16 GB
and no graphics card. Every timing in here was measured rather than looked up,
except where it says otherwise and names the source.*

*Corrected 2026-08-19, when the field report gave the real stream sizes. They
are not FHD: the thermal is **640x512 at 30 fps** and the visible is **640x480
at 15 fps**, both about 1.5 Mb/s. Everything below was re-measured at those
sizes, and the answer changed from "this needs the iGPU and careful budgeting"
to "this fits on one core with room to spare".*

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

**"Just run YOLO on the picture."** Less wrong at 640×512 than it was going to
be at FHD, and still wrong. A detector resizes its input to 640×640, so a
640-wide thermal frame is barely resized at all — but the frame is 512 tall
against 640, so it is stretched, and the person is still whatever the sensor
gave: the code's own figure is about 13 pixels on the thermal head. A model
trained on photographs where a person is hundreds of pixels tall has very little
to say about thirteen, and nothing about the six or seven a smaller target gives.

Cropping is still the answer, and at these sizes it is cheap: a 13-pixel person
inside a 112-pixel crop fed at 192×192 arrives as a **22-pixel** person, for
21 ms.

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

Measured on this development machine, at **the real stream sizes**:

| Stage | Cost | What that is at full rate |
|---|---|---|
| Motion on the thermal, 640×512 | **2.6 ms/frame** | 7.7% of one core at 30 fps |
| Motion on the visible, 640×480 | **2.6 ms/frame** | 3.8% of one core at 15 fps |
| `yolo11n` on one 192×192 crop, **CPU** | 21 ms | 47 crops/sec |
| `yolo11n` on one 320×320 crop, **CPU** | 31 ms | 33 crops/sec |

### The budget for both consoles on the i5

Two consoles, two streams each, motion on all four, at full frame rate:

| | |
|---|---|
| Motion, all four streams | **23% of one core** |
| The i5-12600 has | 6 performance cores and 4 more |

Crop detection on top of that, at five analysed frames a second with an average
of one crop per analysed frame, is **20 crops a second**: about 0.6 of a core at
192×192 on the CPU alone, and a small fraction of the iGPU through OpenVINO.

**It fits, twice over, without the iGPU.** The earlier version of this document
budgeted against FHD and concluded the iGPU was needed; at the real sizes the
crop detector is affordable on the processor alone, and OpenVINO becomes a way
of leaving the CPU free rather than a requirement.

For comparison, the same measurements at the FHD this was first written against:

| Stage | Cost |
|---|---|
| Motion, full frame at 1920×1080 | 19 ms — 53/sec |
| Motion at quarter scale | 1.1 ms |

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

On the 512-line thermal — corrected from the 1080 this was first written
against:

| Sensitivity | Smallest blob accepted | Smallest area |
|---|---|---|
| low | 15 px tall | 150 px² |
| **normal (the default)** | **7.7 px tall** | 40 px² |
| high | 2.6 px tall | 10 px² |

Much less alarming than the FHD arithmetic suggested. A person of 13 px clears
`normal` comfortably; one of 7 px is marginal on height and would need about
7×3 px of area to clear 40, which it does not. **So `high` is still the setting
for the far end of the range on the thermal**, and `normal` is defensible for
the visible.

No detector helps a system whose motion stage is discarding the target before
anything looks at it, and the counters in the report now say whether it is.

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
