"""Writes the sound the console makes when something moves.

    uv run python scripts/make_alarm_sound.py

It produces `vmd/desktop/alarm.wav`, which is committed. This script is not run
on the deployment machine and is not part of installing anything: it is here so
that the sound in the repository is something anybody can read the recipe for
and rebuild, rather than a binary file of unknown origin sitting in a safety
system.

---------------------------------------------------------------------------
Why the console does not use a Windows sound any more
---------------------------------------------------------------------------

"Please also change the sound of the detection to something more alerting."

It was `Windows Proximity Notification.wav`, and he is right about it twice
over. It is a soft two-note chime designed to be unobtrusive - which is the
opposite of the job - and, worse, it is a sound this machine's operator has
heard ten thousand times from things that did not matter. A perimeter alarm that
sounds like an email arriving is a perimeter alarm that gets ignored by a person
who is not doing anything wrong.

So this is deliberately not a notification. It is two tones alternating, which
is what every siren in the world does and what nothing on a Windows desktop
does, and there is no chance of mistaking it for the machine.

---------------------------------------------------------------------------
The choices, and what each of them trades
---------------------------------------------------------------------------

**Two alternating tones rather than one.** A steady tone is heard for a second
and then stops being heard: the ear stops reporting a sound that does not
change. Alternation is what keeps it arriving, and it is why sirens alternate.

**784 Hz and 1175 Hz.** A fifth apart, which reads as an interval rather than as
two unrelated beeps, and both inside the range a room carries well and a cheap
desktop speaker can actually produce. Deliberately not up at 3 kHz where smoke
alarms live: that is more piercing, and an operator sitting beside it for a
twelve-hour shift would turn it off - after which the console is silent again
and nobody knows. The whole point of `Chime.set_enabled` is that switching it
off is a real choice; the sound's job is to not make that choice for him.

**Three alternations, about 1.1 seconds.** Long enough to catch somebody turned
away, short enough to be over before it is annoying, and well short of the
twelve-second quiet period so two alarms never overlap.

**A little third harmonic.** A pure sine is clean and does not carry: small
speakers reproduce it poorly and it disappears under a room's own noise. A
quiet third harmonic gives it enough edge to be heard across a room without
turning it into the buzz of a fire panel.

**Ramped at both ends of every tone.** A waveform that starts at full amplitude
clicks, and a click on a cheap speaker is the loudest part of the sound - so the
alarm would be a series of pops with tones between them.

**Peaks near full scale.** The operator sets the volume; this file's job is not
to be the thing that made it quiet.
"""

from __future__ import annotations

import argparse
import math
import struct
import wave
from pathlib import Path

# CD quality, because it is what every Windows machine plays without resampling
# and the file is only a few hundred kilobytes at this length.
RATE = 44100
CHANNELS = 1
WIDTH_BYTES = 2  # 16-bit signed, which is what winsound expects

# The two tones, in hertz. See the module docstring for why these two.
LOW_HZ = 784.0
HIGH_HZ = 1175.0

# How long each tone is held, and how many of them there are. Six tones is three
# alternations - low, high, low, high, low, high.
TONE_SECONDS = 0.18
TONES = 6

# The silence between tones. Short: a gap long enough to be a gap turns one
# alarm into a sequence of separate beeps, and it is the alternation that has to
# read as one continuous thing.
GAP_SECONDS = 0.01

# How quickly each tone reaches full amplitude and comes back down again, in
# seconds. Five milliseconds is inaudible as a fade and is enough to remove the
# click of a waveform that starts at full scale.
RAMP_SECONDS = 0.005

# How loud, as a share of full scale, and how much third harmonic is mixed in.
# 0.9 leaves headroom so that the sum of the fundamental and the harmonic cannot
# clip - a clipped waveform is a buzz, and it would be the one part of this that
# sounds broken rather than urgent.
PEAK = 0.9
HARMONIC = 0.18


def tone(frequency: float, seconds: float) -> list[float]:
    """One tone, ramped at both ends, as samples between -1 and 1."""
    count = int(RATE * seconds)
    ramp = max(1, int(RATE * RAMP_SECONDS))
    samples: list[float] = []
    for index in range(count):
        angle = 2.0 * math.pi * frequency * (index / RATE)
        value = math.sin(angle) + HARMONIC * math.sin(3.0 * angle)
        # Normalised by the worst case the mix can reach rather than measured,
        # so the amplitude does not depend on where the two waves happen to line
        # up in a tone of this particular length.
        value /= 1.0 + HARMONIC
        if index < ramp:
            value *= index / ramp
        elif index > count - ramp:
            value *= max(0.0, (count - index) / ramp)
        samples.append(value * PEAK)
    return samples


def alarm() -> list[float]:
    """The whole sound: tones alternating, with a hair of silence between."""
    gap = [0.0] * int(RATE * GAP_SECONDS)
    samples: list[float] = []
    for index in range(TONES):
        samples.extend(tone(HIGH_HZ if index % 2 else LOW_HZ, TONE_SECONDS))
        if index < TONES - 1:
            samples.extend(gap)
    return samples


def write(path: Path, samples: list[float]) -> None:
    frames = b"".join(
        struct.pack("<h", max(-32767, min(32767, int(value * 32767)))) for value in samples
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(CHANNELS)
        out.setsampwidth(WIDTH_BYTES)
        out.setframerate(RATE)
        out.writeframes(frames)


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(here / "vmd" / "desktop" / "alarm.wav"),
        help="where to write it (default: the file the console plays)",
    )
    args = parser.parse_args()

    path = Path(args.out)
    samples = alarm()
    write(path, samples)
    print(f"wrote {path}")
    print(f"  {len(samples) / RATE:.2f} seconds, {RATE} Hz, {WIDTH_BYTES * 8}-bit mono")
    print(f"  {path.stat().st_size / 1024:.0f} KB")
    print(f"  {LOW_HZ:.0f} Hz and {HIGH_HZ:.0f} Hz alternating, {TONES} tones")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
