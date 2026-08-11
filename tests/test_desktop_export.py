"""Saving a piece of the archive to a folder, without spawning anything.

The real ffmpeg run is in `tests/test_desktop_export_integration.py`, because a
clip that cannot be opened again is the worst failure this feature has and only
a real run proves it did not happen. Everything here is what is decided before
ffmpeg is started and what is said afterwards - the two halves that can be wrong
without anybody noticing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from vmd.desktop.export import (
    ExportOutcome,
    clip_command,
    concat_list,
    export_clip,
    suggested_name,
    unique_path,
)
from vmd.desktop.timeline import ClipPart, ClipPlan


def plan(*parts: ClipPart, requested: float = 0.0, gaps=()) -> ClipPlan:
    requested = requested or sum(p.duration for p in parts)
    return ClipPlan(parts=list(parts), gaps=list(gaps), requested_seconds=requested)


class Recorder:
    """A stand-in for subprocess.run that remembers what it was asked to do."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.commands: list[list[str]] = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        # Whatever it was told to write, written, so that the outcome can be
        # about the command rather than about a missing file.
        Path(command[-1]).write_bytes(b"a clip")
        return subprocess.CompletedProcess(command, self.returncode, b"", self.stderr)


# ------------------------------------------------------- what ffmpeg is handed


def test_the_list_names_the_file_and_the_piece_of_it_wanted() -> None:
    text = concat_list([ClipPart(path="C:/rec/a.mp4", start_offset=280.0, duration=20.0)])
    assert "file 'C:/rec/a.mp4'" in text
    assert "inpoint 280" in text
    assert "outpoint 300" in text


def test_a_clip_across_several_files_lists_them_in_order() -> None:
    text = concat_list(
        [
            ClipPart(path="a.mp4", start_offset=280.0, duration=20.0),
            ClipPart(path="b.mp4", start_offset=0.0, duration=300.0),
            ClipPart(path="c.mp4", start_offset=0.0, duration=10.0),
        ]
    )
    assert text.index("a.mp4") < text.index("b.mp4") < text.index("c.mp4")


def test_a_backslash_path_is_written_the_way_the_list_reader_wants_it() -> None:
    """Every path in this index is a Windows path, and the concat reader takes
    a backslash as an escape."""
    text = concat_list([ClipPart(path="C:\\rec\\thermal\\a.mp4", start_offset=0.0, duration=1.0)])
    assert "\\" not in text


def test_a_quote_in_a_path_is_escaped_rather_than_ending_the_name() -> None:
    """A folder with an apostrophe in it would otherwise end the quoted name
    half way through and ffmpeg would be handed two files that do not exist."""
    text = concat_list([ClipPart(path="D:/Noam's clips/a.mp4", start_offset=0.0, duration=1.0)])
    assert "Noam" in text
    assert text.count("file '") == 1
    assert "'\\''" in text


def test_nothing_is_re_encoded() -> None:
    command = clip_command("ffmpeg", Path("list.txt"), Path("out.mp4"))
    assert "-c" in command and command[command.index("-c") + 1] == "copy"
    assert not any(part.startswith("libx26") for part in command)


def test_the_destination_is_the_last_thing_on_the_command() -> None:
    command = clip_command("ffmpeg", Path("list.txt"), Path("D:/clips/out.mp4"))
    assert command[-1] == str(Path("D:/clips/out.mp4"))


# --------------------------------------------------------------- the exporting


def test_a_clip_inside_one_file_is_written_and_named(tmp_path: Path) -> None:
    run = Recorder()
    destination = tmp_path / "clip.mp4"
    outcome = export_clip(
        plan(ClipPart(path=str(tmp_path / "a.mp4"), start_offset=10.0, duration=20.0)),
        destination=destination,
        stream="thermal",
        ffmpeg="ffmpeg",
        run=run,
    )
    assert outcome.ok
    assert outcome.path == destination
    assert str(destination) in outcome.message
    assert len(run.commands) == 1


def test_a_clip_over_nothing_at_all_is_refused_and_nothing_is_started(
    tmp_path: Path,
) -> None:
    run = Recorder()
    outcome = export_clip(
        plan(requested=600.0, gaps=[(0.0, 600.0)]),
        destination=tmp_path / "clip.mp4",
        stream="thermal",
        ffmpeg="ffmpeg",
        run=run,
    )
    assert not outcome.ok
    assert run.commands == []
    assert "no recording" in outcome.message.lower(), outcome.message
    assert not (tmp_path / "clip.mp4").exists()


def test_a_clip_crossing_a_gap_says_it_is_shorter_than_what_was_asked_for(
    tmp_path: Path,
) -> None:
    """The one thing he must not find out by watching it: the range he dragged
    was ten minutes and what he has is four."""
    run = Recorder()
    outcome = export_clip(
        plan(
            ClipPart(path=str(tmp_path / "a.mp4"), start_offset=0.0, duration=240.0),
            requested=600.0,
            gaps=[(0.0, 360.0)],
        ),
        destination=tmp_path / "clip.mp4",
        stream="thermal",
        ffmpeg="ffmpeg",
        run=run,
    )
    assert outcome.ok
    said = outcome.message.lower()
    assert "shorter" in said or "missing" in said, outcome.message
    assert "6m" in outcome.message or "6 m" in outcome.message, outcome.message


def test_a_whole_clip_does_not_warn_about_a_gap(tmp_path: Path) -> None:
    run = Recorder()
    outcome = export_clip(
        plan(ClipPart(path=str(tmp_path / "a.mp4"), start_offset=0.0, duration=60.0)),
        destination=tmp_path / "clip.mp4",
        stream="thermal",
        ffmpeg="ffmpeg",
        run=run,
    )
    assert "shorter" not in outcome.message.lower(), outcome.message


def test_ffmpeg_refusing_is_a_sentence_and_not_a_traceback(tmp_path: Path) -> None:
    run = Recorder(returncode=1, stderr="Invalid data found when processing input")
    outcome = export_clip(
        plan(ClipPart(path=str(tmp_path / "a.mp4"), start_offset=0.0, duration=60.0)),
        destination=tmp_path / "clip.mp4",
        stream="thermal",
        ffmpeg="ffmpeg",
        run=run,
    )
    assert not outcome.ok
    assert "could not be saved" in outcome.message.lower(), outcome.message


def test_a_clip_that_failed_leaves_nothing_behind_pretending_to_be_footage(
    tmp_path: Path,
) -> None:
    """Half a file with the name he chose is worse than no file: he will find
    it in six months and believe it is the evidence."""
    run = Recorder(returncode=1, stderr="No space left on device")
    destination = tmp_path / "clip.mp4"
    export_clip(
        plan(ClipPart(path=str(tmp_path / "a.mp4"), start_offset=0.0, duration=60.0)),
        destination=destination,
        stream="thermal",
        ffmpeg="ffmpeg",
        run=run,
    )
    assert not destination.exists()


def test_a_folder_that_cannot_be_written_to_is_a_sentence(tmp_path: Path) -> None:
    outcome = export_clip(
        plan(ClipPart(path=str(tmp_path / "a.mp4"), start_offset=0.0, duration=60.0)),
        destination=tmp_path / "nothing" / "deeper" / "clip.mp4",
        stream="thermal",
        ffmpeg="ffmpeg",
        run=Recorder(),
        make_folder=_refusing_folder,
    )
    assert not outcome.ok
    assert "could not" in outcome.message.lower(), outcome.message


def _refusing_folder(path: Path) -> None:
    raise OSError(28, "There is not enough space on the disk")


def test_ffmpeg_that_is_not_there_at_all_is_a_sentence(tmp_path: Path) -> None:
    def missing(command, **kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    outcome = export_clip(
        plan(ClipPart(path=str(tmp_path / "a.mp4"), start_offset=0.0, duration=60.0)),
        destination=tmp_path / "clip.mp4",
        stream="thermal",
        ffmpeg="ffmpeg",
        run=missing,
    )
    assert not outcome.ok
    assert outcome.message.strip()


def test_the_list_file_is_cleared_up_whatever_happened(tmp_path: Path) -> None:
    run = Recorder(returncode=1)
    export_clip(
        plan(ClipPart(path=str(tmp_path / "a.mp4"), start_offset=0.0, duration=60.0)),
        destination=tmp_path / "clip.mp4",
        stream="thermal",
        ffmpeg="ffmpeg",
        run=run,
    )
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".txt"] == []


# ------------------------------------------------------------ what it is called


def test_the_suggested_name_carries_the_stream_and_the_time() -> None:
    import datetime

    start = datetime.datetime(2026, 8, 11, 14, 32, 5).timestamp()
    name = suggested_name("thermal", start, start + 65)
    assert "thermal" in name
    assert "2026-08-11" in name
    assert name.endswith(".mp4")


def test_the_suggested_name_holds_nothing_windows_refuses() -> None:
    import datetime

    start = datetime.datetime(2026, 8, 11, 14, 32, 5).timestamp()
    name = suggested_name('the "north" gate: 1/2', start, start + 65)
    for refused in '<>:"/\\|?*':
        assert refused not in name, name


def test_a_name_already_taken_is_not_written_over(tmp_path: Path) -> None:
    """He saved a clip of this minute last week. Overwriting it silently is
    losing evidence to a naming rule."""
    (tmp_path / "clip.mp4").write_bytes(b"the first one")
    chosen = unique_path(tmp_path, "clip.mp4")
    assert chosen != tmp_path / "clip.mp4"
    assert chosen.suffix == ".mp4"
    assert not chosen.exists()


def test_a_free_name_is_left_alone(tmp_path: Path) -> None:
    assert unique_path(tmp_path, "clip.mp4") == tmp_path / "clip.mp4"


def test_an_outcome_always_says_something() -> None:
    """This sentence is the whole of what the operator gets. There is no
    dialogue box behind it and no log he can open."""
    assert ExportOutcome(ok=False, path=None, message="x").message
