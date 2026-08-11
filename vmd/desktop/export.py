"""Cutting a piece of the archive out to a file the operator keeps.

Everything in the recordings folder is on a laptop that deletes its own oldest
footage to stay inside a budget, so the clip of the thing that mattered is
already on a clock. Saving it somewhere he chooses is how it stops being.

**Nothing is re-encoded.** The segments are H.264 exactly as the camera sent
them, and `-c copy` copies the packets across. That makes an hour's clip a
disk-to-disk copy rather than an hour of a laptop that is also recording two
streams and watching for movement - and it means what he keeps is bit for bit
what was recorded, which is the only version worth keeping.

The cost of copying rather than re-encoding is that a cut can only land on a
keyframe, so a clip may begin a moment before the mark. That is said out loud
rather than hidden: it is a second or two, in his favour, and the alternative is
a machine that spends ten minutes and a lot of heat to move a cut by one frame.

**Several files are one command.** A range longer than a segment, or one that
lands across a boundary, is written as a concat list with an `inpoint` and an
`outpoint` per file, so one ffmpeg reads the pieces in order and writes one
clip. There are no intermediate files to leave behind on a disk that may be the
reason the operator is saving anything at all.

**A gap is not silently swallowed.** `clip_plan` has already worked out what is
on disk inside the range; the pieces are what exist, so a range crossing an hour
with no recording produces a shorter clip, and the sentence afterwards says so.
A clip he believes is ten minutes when it is four is worse than no clip.
"""

from __future__ import annotations

import datetime
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from vmd.desktop.timeline import ClipPart, ClipPlan
from vmd.storage.recorder import find_ffmpeg

logger = logging.getLogger(__name__)

# How long to wait for one clip before deciding ffmpeg is not coming back.
#
# A clip is a copy, so it runs at disk speed: a night's footage is a couple of
# minutes. This is not a performance budget - it is the number that stops a
# wedged ffmpeg from holding a worker thread of a console that runs for months.
EXPORT_TIMEOUT_SECONDS = 900.0

# What a Windows filename may not contain, plus the control characters, plus the
# trailing dot and space Explorer quietly drops.
_REFUSED = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class ExportOutcome:
    """What happened, and the one sentence the operator gets about it.

    There is no dialogue behind this and no log he can open, so the message is
    the whole of what he is told. It always says where the file went when there
    is one, because "saved" without a place is not an answer on a machine with
    four drives.
    """

    ok: bool
    path: Path | None
    message: str


# ------------------------------------------------------------ what ffmpeg reads


def concat_list(parts: Iterable[ClipPart]) -> str:
    """The concat demuxer's own list, naming each file and the piece wanted.

    Forward slashes, because the demuxer reads a backslash inside a quoted name
    as an escape and every path in this index is a Windows path. An apostrophe
    in a folder name is escaped rather than left to end the quoted name half way
    through - a real folder called `Noam's clips` would otherwise hand ffmpeg
    two files that do not exist.

    `inpoint`/`outpoint` are seconds inside each file, which is what the plan
    already carries: the recorder writes segments with `-reset_timestamps 1`, so
    every segment starts at zero and a file's own clock is its offset from its
    own beginning.
    """
    lines: list[str] = []
    for part in parts:
        name = Path(part.path).as_posix().replace("'", "'\\''")
        lines.append(f"file '{name}'")
        lines.append(f"inpoint {part.start_offset:.3f}")
        lines.append(f"outpoint {part.start_offset + part.duration:.3f}")
    return "\n".join(lines) + "\n"


def clip_command(ffmpeg: str, list_path: Path, destination: Path) -> list[str]:
    """One ffmpeg, reading a list of pieces and writing one file.

    `-safe 0` because the paths are absolute; `-protocol_whitelist file`
    because a list file may only ever name files on this disk, and this console
    is offline on purpose. `-avoid_negative_ts make_zero` starts the clip's own
    clock at zero, without which a player opening it shows the time of day the
    footage was recorded and a scrubber that begins part-way along.
    """
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-protocol_whitelist",
        "file",
        "-i",
        str(list_path),
        "-map",
        "0:v:0",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        # So the clip opens instantly rather than after the whole of it has been
        # read: he double-clicks this in Explorer, on a laptop, in a hurry.
        "-movflags",
        "+faststart",
        str(destination),
    ]


# ------------------------------------------------------------- what it is called


def suggested_name(stream: str, start: float, end: float) -> str:
    """A filename that says what this is without being opened.

    Nothing Windows refuses survives, and nothing is left that Explorer would
    silently rename. The stream comes from settings, which is a field a person
    types into, so it is cleaned rather than trusted.
    """
    began = datetime.datetime.fromtimestamp(start)
    finished = datetime.datetime.fromtimestamp(end)
    label = _REFUSED.sub(" ", str(stream)).strip(" .") or "camera"
    label = " ".join(label.split())
    return (
        f"{label} {began.strftime('%Y-%m-%d %H-%M-%S')} "
        f"to {finished.strftime('%H-%M-%S')}.mp4"
    )


def unique_path(folder: Path, name: str) -> Path:
    """That name in that folder, or the next one that is free.

    He saved a clip of this minute last week; writing over it silently would
    lose evidence to a naming rule. Bounded rather than a while-true, because
    this runs on the thread of a console.
    """
    folder = Path(folder)
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate = folder / name
    for number in range(2, 1000):
        if not candidate.exists():
            return candidate
        candidate = folder / f"{stem} ({number}){suffix}"
    return candidate


# --------------------------------------------------------------- the exporting


def export_clip(
    plan: ClipPlan,
    destination: Path,
    stream: str,
    ffmpeg: str | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    make_folder: Callable[[Path], None] | None = None,
) -> ExportOutcome:
    """Write the planned pieces to one file. Never raises.

    Called from a worker, and the answer is a sentence rather than an exception
    because the only thing waiting for it is a line under a button. Every way
    this can go wrong on the machine it ships to - a folder that is not there, a
    disk with no room, a memory stick pulled out, an ffmpeg that was never
    installed - ends here as words.
    """
    destination = Path(destination)
    if not plan.parts:
        return ExportOutcome(
            ok=False,
            path=None,
            message=(
                f"Nothing was saved: there is no recording on {stream} in the "
                "part of the day that was marked."
            ),
        )

    make_folder = make_folder or _make_folder
    try:
        make_folder(destination.parent)
    except OSError as error:
        logger.warning("could not prepare %s", destination.parent, exc_info=True)
        return ExportOutcome(
            ok=False,
            path=None,
            message=f"Nothing was saved: {destination.parent} could not be written to ({error.strerror or error}).",
        )

    ffmpeg = ffmpeg or find_ffmpeg()
    try:
        list_path = _write_list(plan.parts, destination.parent)
    except OSError as error:
        logger.warning("could not write the list of pieces", exc_info=True)
        return ExportOutcome(
            ok=False,
            path=None,
            message=f"Nothing was saved: {destination.parent} could not be written to ({error.strerror or error}).",
        )

    try:
        return _spawn(plan, list_path, destination, stream, ffmpeg, run)
    finally:
        try:
            list_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("could not remove %s", list_path, exc_info=True)


def _make_folder(folder: Path) -> None:
    Path(folder).mkdir(parents=True, exist_ok=True)


def _write_list(parts: Sequence[ClipPart], folder: Path) -> Path:
    handle, name = tempfile.mkstemp(dir=str(folder), prefix="clip.", suffix=".txt")
    with os.fdopen(handle, "w", encoding="utf-8") as file:
        file.write(concat_list(parts))
    return Path(name)


def _spawn(
    plan: ClipPlan,
    list_path: Path,
    destination: Path,
    stream: str,
    ffmpeg: str,
    run: Callable[..., subprocess.CompletedProcess],
) -> ExportOutcome:
    command = clip_command(ffmpeg, list_path, destination)
    try:
        finished = run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=EXPORT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        logger.exception("the video tool could not be started")
        return _failed(
            destination,
            "Nothing was saved: the part of VMD that writes clips is missing. "
            "Reinstall VMD.",
        )
    except subprocess.TimeoutExpired:
        logger.error("saving a clip did not finish in %s s", EXPORT_TIMEOUT_SECONDS)
        return _failed(destination, "Nothing was saved: writing the clip did not finish.")
    except OSError as error:
        logger.exception("saving a clip failed")
        return _failed(destination, f"Nothing was saved: {error.strerror or error}.")

    if getattr(finished, "returncode", 1) != 0:
        return _failed(
            destination,
            f"The clip could not be saved. {_last_line(getattr(finished, 'stderr', ''))}".strip(),
        )
    if not destination.exists():
        return _failed(destination, "The clip could not be saved: nothing was written.")

    said = f"Saved {_duration(plan.covered_seconds)} of {stream} to {destination}."
    if not plan.whole:
        said += (
            f" It is shorter than the part that was marked: {_duration(plan.missing_seconds)} "
            "of it has no recording."
        )
    return ExportOutcome(ok=True, path=destination, message=said)


def _failed(destination: Path, message: str) -> ExportOutcome:
    """Say what happened, and leave nothing behind pretending to be footage.

    ffmpeg creates the output before it discovers it cannot write it, so a
    failure leaves a file with the name the operator chose and nothing in it. He
    finds it in six months and believes it is the evidence.
    """
    try:
        destination.unlink(missing_ok=True)
    except OSError:
        logger.debug("could not remove the unfinished %s", destination, exc_info=True)
    return ExportOutcome(ok=False, path=None, message=message)


def _last_line(stderr) -> str:
    """ffmpeg's own last word, if it said anything worth passing on."""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    lines = [line.strip() for line in str(stderr or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
