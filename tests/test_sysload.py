"""The processor/memory readout's arithmetic, away from the kernel and Qt.

The reading of the kernel is a `ctypes` call and is left to the machine; what is
tested here is the one part with a decision in it - turning two running totals
into a load over the gap between them - and the line the band draws from it.
"""

from vmd.desktop.sysload import (
    CPU_WALL_PERCENT,
    Load,
    cpu_busy_percent,
    format_load,
)


def test_the_first_reading_has_no_rate_to_report():
    # A load is a rate, and the first sample of a run has nothing to measure
    # against - so None, and the line shows a dash until the second tick.
    assert cpu_busy_percent(None, (10, 100)) is None


def test_two_readings_in_the_same_tick_are_not_a_divide_by_zero():
    assert cpu_busy_percent((50, 100), (50, 100)) is None


def test_a_fully_busy_gap_reads_one_hundred():
    # 100 ticks passed, none of them idle.
    assert cpu_busy_percent((50, 100), (50, 200)) == 100.0


def test_a_fully_idle_gap_reads_zero():
    # 100 ticks passed, all of them idle.
    assert cpu_busy_percent((50, 100), (150, 200)) == 0.0


def test_a_half_busy_gap_reads_fifty():
    assert cpu_busy_percent((50, 100), (100, 200)) == 50.0


def test_a_load_outside_zero_to_a_hundred_is_clamped():
    # The deltas can disagree by a tick at the edges; a load below zero or above
    # a hundred is a wrong answer however small, so it is clamped rather than
    # drawn.
    assert cpu_busy_percent((0, 100), (200, 200)) == 0.0
    assert cpu_busy_percent((100, 100), (0, 200)) == 100.0


def test_the_wall_is_where_the_readout_turns_red():
    assert Load(CPU_WALL_PERCENT, 50.0, 4.0, 8.0).cpu_at_wall
    assert not Load(CPU_WALL_PERCENT - 1, 50.0, 4.0, 8.0).cpu_at_wall
    # No reading is not at the wall - a dash never alarms.
    assert not Load(None, 50.0, 4.0, 8.0).cpu_at_wall


def test_a_machine_that_cannot_answer_draws_nothing():
    # None in, empty line out, so the readout takes no room where it cannot read.
    assert format_load(None) == ""


def test_the_line_shows_a_dash_until_the_rate_has_two_samples():
    line = format_load(Load(None, 69.0, 22.1, 31.9))
    assert "CPU --" in line
    assert "RAM 69%" in line


def test_the_line_is_whole_percents_and_one_decimal_of_gigabytes():
    line = format_load(Load(13.4, 68.7, 22.05, 31.93))
    # Whole percents - a decimal that moves every second is noise at two metres.
    assert "CPU 13%" in line
    assert "RAM 69%" in line
    # Gigabytes to one place, so the used figure is legible without being jumpy.
    assert "22.1/31.9 GB" in line
