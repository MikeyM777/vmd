"""What the detector threw away, and why, in words.

"The VMD is shit... it's marking steady and static things... no movement."

Something is wrong and nothing on this console can say what. The detector counts
every blob it sees and every rule that rejected one - `blobs`, `rejected`,
`suppressed`, `events` are all in `detection.json` already, written every few
seconds - and not one of those numbers has ever been shown to anybody. The
comment beside them in `vmd/detect/pipeline.py` says they exist so that a rule
which has rejected everything "is now a number rather than a guess", and then
nothing ever read them.

That is the gap this file closes. It turns those counters into sentences that
name the fault, because the four shapes this can take call for four completely
different fixes and they are indistinguishable from the outside:

  NOTHING SEEN         the motion stage produces no blobs at all. The camera,
                       the address or the picture - not the settings.
  EVERYTHING REJECTED  blobs are found and every one is thrown away. The size
                       rule and the ignore areas are the usual culprits, and
                       this is the shape that silently deletes real people.
  EVERYTHING REPORTED  blobs are found, almost nothing is rejected, and almost
                       all of it becomes an event. This is the one he has: a
                       treeline, sensitivity too high, or an area that should
                       have been painted out.
  SUPPRESSED           whole frames are being discarded because too much of the
                       picture is moving. The camera is moving - wind on the
                       mast, or a PTZ that never settles.

Nothing here decides anything or changes any setting. It reads numbers the
detector already publishes and says what they mean.
"""

from __future__ import annotations

# How lopsided a reading has to be before it is worth naming.
#
# Nine tenths. A rule that rejects most of what it sees is doing its job; a rule
# that rejects nine out of ten of everything the camera has ever produced is
# either wrong or pointed at the wrong scene, and either way it is worth a
# sentence. Below this there is nothing useful to say and saying something
# anyway is how a diagnostic becomes noise.
LOPSIDED = 0.9

# How few blobs make "nothing is being seen" a fair thing to say. Not zero: a
# detector that has produced three blobs in an hour is as blind as one that has
# produced none, and the difference between them is not worth two sentences.
BARELY_ANY = 10

# What each rejection rule is called in front of somebody who has never read the
# source. The keys are `REJECTION_RULES` in vmd/detect/pipeline.py.
RULE_WORDS = {
    "ignore_mask": "inside an area you painted out",
    "horizon": "above the sky line",
    "too_small": "smaller than the size setting allows",
    "too_large": "bigger than the size setting allows",
}


def lines(state: dict) -> list[str]:
    """One stream's counters, as sentences. Empty when there is nothing to say.

    `state` is one entry of what the detector publishes into detection.json.
    Everything is read with `.get`: this runs against a file written by another
    process, which may be older than this code, and a missing key must cost a
    sentence rather than the report.
    """
    blobs = _count(state.get("blobs"))
    events = _count(state.get("events"))
    suppressed = _count(state.get("suppressed"))
    frames = _count(state.get("frames"))
    rejected = state.get("rejected")
    rejected = rejected if isinstance(rejected, dict) else {}
    thrown = sum(_count(value) for value in rejected.values())

    said: list[str] = [
        f"looked at {frames:,} frames, found {blobs:,} things that moved, "
        f"threw away {thrown:,}, reported {events:,}"
    ]

    if frames <= 0:
        return ["no frames have reached the detector at all"]

    if blobs <= BARELY_ANY:
        said.append(
            "NOTHING IS BEING SEEN. The detector is reading frames and finding "
            "no movement in any of them. That is the camera, the address or the "
            "picture itself - not the detection settings."
        )
        return said

    # The one he is reporting: nearly everything that moves becomes an event.
    if events and blobs and events >= LOPSIDED * (blobs - thrown) and thrown < LOPSIDED * blobs:
        said.append(
            "NEARLY EVERYTHING IS BEING REPORTED. Almost nothing is being "
            "thrown away, so anything that moves at all becomes an alarm. Paint "
            "out what is never news, or turn the sensitivity down a step."
        )

    if thrown >= LOPSIDED * blobs:
        worst = max(rejected.items(), key=lambda pair: _count(pair[1]), default=None)
        why = RULE_WORDS.get(worst[0], worst[0]) if worst else "a rule"
        said.append(
            f"NEARLY EVERYTHING IS BEING THROWN AWAY - {thrown:,} of {blobs:,}, "
            f"most of it {why}. If real movement is being missed, this is where "
            f"it is going."
        )

    if suppressed and frames and suppressed >= 0.2 * frames:
        said.append(
            f"{suppressed:,} whole frames were discarded because too much of the "
            f"picture was moving at once - that is the camera moving, not the "
            f"scene. Wind on the mast, or a camera that never settles after a "
            f"move."
        )

    for rule, count in sorted(rejected.items(), key=lambda pair: -_count(pair[1])):
        count = _count(count)
        if count <= 0:
            continue
        said.append(f"  thrown away {count:,} {RULE_WORDS.get(rule, rule)}")

    return said


def _count(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
