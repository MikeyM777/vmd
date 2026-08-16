"""The picture of what moved, with the box on it.

"After a couple of seconds it beeped and no box was on the screen."

The box is not on the live picture and cannot be - libVLC draws those into a
native window and nothing goes over one. It is on a still of the frame the
detector confirmed on, which is also where it belongs: on any later frame the
box would be beside the thing rather than on it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from vmd.detect.stills import (
    KEEP_PER_STREAM,
    LONGEST_EDGE,
    folder_for,
    prune,
    save,
    still_for,
)


def scene(height: int = 1080, width: int = 1920):
    """A picture with a horizon and something bright standing on it."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[: height // 2] = 40
    frame[height // 2 :] = 90
    frame[height // 2 - 60 : height // 2 + 40, width // 2 : width // 2 + 40] = 230
    return frame


def test_the_still_lands_where_the_console_will_look_for_it(tmp_path: Path) -> None:
    """The whole reason this is not a column in the database: both sides work
    the path out from what the event already carries."""
    written = save(scene(), (940, 480, 50, 110), tmp_path, "thermal", 1755300000.123)
    assert written is not None
    assert written == still_for(tmp_path, "thermal", 1755300000.123)
    assert written.is_file()


def test_the_still_is_small_enough_to_be_written_during_an_alarm(tmp_path: Path) -> None:
    """The camera is 4K and this is looked at in a strip a few hundred pixels
    wide. A 4K JPEG would cost a megabyte and a tenth of a second on the thread
    that is meant to be watching the perimeter."""
    import cv2

    written = save(scene(2160, 3840), (940, 480, 50, 110), tmp_path, "thermal", 1.0)
    assert written is not None
    assert written.stat().st_size < 400_000

    picture = cv2.imread(str(written))
    assert max(picture.shape[:2]) == LONGEST_EDGE


def test_the_box_is_actually_drawn_on_it(tmp_path: Path) -> None:
    """The one thing this picture exists for. A still saved without the mark
    would look completely normal and answer nothing."""
    import cv2

    plain = scene()
    written = save(plain, (940, 480, 50, 110), tmp_path, "thermal", 1.0)
    assert written is not None
    marked = cv2.imread(str(written))

    scale = LONGEST_EDGE / max(plain.shape[:2])
    same = cv2.resize(
        plain,
        (int(plain.shape[1] * scale), int(plain.shape[0] * scale)),
        interpolation=cv2.INTER_AREA,
    )
    assert marked.shape == same.shape
    # The frame is unchanged everywhere except around the box.
    difference = cv2.absdiff(marked, same).max(axis=2)
    assert difference.max() > 60, "nothing was drawn on it at all"
    changed = np.argwhere(difference > 60)
    top, left = changed.min(axis=0)
    bottom, right = changed.max(axis=0)
    # Where the box was, in the resized picture, give or take the outline.
    assert abs(left - 940 * scale) < 12, f"the mark is at x={left}, not {940 * scale:.0f}"
    assert abs(top - 480 * scale) < 12, f"the mark is at y={top}, not {480 * scale:.0f}"
    assert abs(right - (940 + 50) * scale) < 12
    assert abs(bottom - (480 + 110) * scale) < 12


def test_the_source_frame_is_not_written_on(tmp_path: Path) -> None:
    """The detector goes on comparing this frame with the ones after it. A box
    painted into it would be movement in every frame that followed."""
    frame = scene()
    before = frame.copy()
    save(frame, (940, 480, 50, 110), tmp_path, "thermal", 1.0)
    assert np.array_equal(frame, before)


def test_the_folder_keeps_only_the_newest(tmp_path: Path) -> None:
    """This process runs for months and a windy night is forty events an hour."""
    frame = scene(240, 320)
    for index in range(KEEP_PER_STREAM + 20):
        save(frame, (10, 10, 20, 20), tmp_path, "thermal", 1000.0 + index)

    kept = sorted(folder_for(tmp_path, "thermal").glob("*.jpg"), key=lambda p: int(p.stem))
    assert len(kept) == KEEP_PER_STREAM
    # The newest, and named by when the movement started rather than by when the
    # file happened to be written: this machine's clock is set by hand.
    assert int(kept[-1].stem) == int((1000.0 + KEEP_PER_STREAM + 19) * 1000)


def test_pruning_goes_by_the_name_and_not_by_the_files_own_time(tmp_path: Path) -> None:
    """A file's timestamp here is whatever the clock said when it was written,
    on a machine whose clock can go backwards by an hour."""
    folder = folder_for(tmp_path, "thermal")
    folder.mkdir(parents=True)
    for moment in (5000, 1000, 3000):
        (folder / f"{moment}.jpg").write_bytes(b"not really a jpeg")

    assert prune(tmp_path, "thermal", keep=1) == 2
    assert [path.stem for path in folder.glob("*.jpg")] == ["5000"]


def test_a_stream_name_cannot_put_the_folder_somewhere_else(tmp_path: Path) -> None:
    """Stream names are typed by the operator. They have never had to be a path
    before, and one with a slash in it would write outside the recordings."""
    folder = folder_for(tmp_path, "../../thermal")
    assert tmp_path in folder.parents
    assert ".." not in folder.parts


def test_a_stream_named_only_in_punctuation_still_gets_a_folder(tmp_path: Path) -> None:
    assert folder_for(tmp_path, "///").name == "stream"


@pytest.mark.parametrize(
    "frame, box",
    [
        (None, (10, 10, 20, 20)),
        (np.zeros((0, 0, 3), dtype=np.uint8), (10, 10, 20, 20)),
    ],
)
def test_nothing_worth_saving_is_not_an_error(tmp_path: Path, frame, box) -> None:
    """A still is a convenience and the event is the product. Every way this can
    fail has to cost the picture and nothing else."""
    assert save(frame, box, tmp_path, "thermal", 1.0) is None


def test_a_folder_that_cannot_be_written_costs_the_picture_and_nothing_else(
    tmp_path: Path, monkeypatch
) -> None:
    """A full disk is a perfectly ordinary state on this machine, and it is one
    of the things this console exists to report - not to fall over on."""
    import vmd.detect.stills as stills

    def full(*args, **kwargs):
        raise OSError("There is not enough space on the disk")

    monkeypatch.setattr(Path, "mkdir", full)
    assert stills.save(scene(240, 320), (10, 10, 20, 20), tmp_path, "thermal", 1.0) is None


def test_pruning_a_folder_that_is_not_there_is_quiet(tmp_path: Path) -> None:
    assert prune(tmp_path, "thermal") == 0
