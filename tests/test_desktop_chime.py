"""The sound an alarm makes, and every rule about when it does not.

`DESIGN.md` has promised since it was written that an arriving alarm makes a
sound, and nothing in the program made one. This console runs 24/7 in a room,
watching a perimeter 700 m away, and its alarm strip is red, wide and completely
silent - so an intrusion at 03:40 while he is turned away is announced to an
empty chair and cleared by the next thing that moves.

What is tested here is mostly the restraint rather than the sound. A chime that
gets this wrong is worse than no chime, and there are three ways to get it
wrong, all of them ending with the speakers unplugged and the console silent
again with nobody knowing:

* it plays on the thread that draws the window, and the window stops repainting
  during an alarm;
* it plays forty times on a windy night;
* it cannot be switched off, so somebody switches off the speakers instead.
"""

from __future__ import annotations

from vmd.desktop.chime import QUIET_SECONDS, Chime


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


def counting():
    """A player that writes down that it was asked, and returns at once."""
    sounds: list[int] = []
    return sounds, lambda: sounds.append(1)


# ------------------------------------------------------------------ it sounds


def test_something_moving_makes_a_sound() -> None:
    sounds, player = counting()
    chime = Chime(player=player, clock=Clock())
    assert chime.alarm() is True
    assert len(sounds) == 1


def test_a_second_alarm_after_the_quiet_time_sounds_again() -> None:
    """A separate intrusion a quarter of a minute later is a separate thing to
    be told about."""
    sounds, player = counting()
    clock = Clock()
    chime = Chime(player=player, clock=clock)
    chime.alarm()
    clock.tick(QUIET_SECONDS + 1)
    assert chime.alarm() is True
    assert len(sounds) == 2


# ------------------------------------------------------------- it stays quiet


def test_a_windy_night_is_one_chime_and_not_forty() -> None:
    """Movement in a treeline is not one event, it is forty. Forty chimes in a
    minute is a console somebody turns the speakers off for - after which it is
    silent again, and now nobody knows."""
    sounds, player = counting()
    clock = Clock()
    chime = Chime(player=player, clock=clock)
    for _ in range(40):
        clock.tick(1.5)
        chime.alarm()
    assert len(sounds) <= 6, f"{len(sounds)} chimes in a minute"
    assert chime.held_back > 0


def test_holding_back_is_counted_rather_than_hidden() -> None:
    """"An alarm arrived and the room was not told" is a thing somebody
    investigating a missed intrusion has to be able to read."""
    sounds, player = counting()
    chime = Chime(player=player, clock=Clock())
    chime.alarm()
    assert chime.alarm() is False
    assert chime.played == 1 and chime.held_back == 1


def test_switched_off_means_silent_and_says_so() -> None:
    """Somebody asleep in the same room has a good reason. Taking the choice
    away is how the speakers get unplugged instead, which is the same silence
    with nobody in charge of it."""
    sounds, player = counting()
    chime = Chime(player=player, clock=Clock(), enabled=False)
    assert chime.alarm() is False
    assert sounds == []
    chime.set_enabled(True)
    assert chime.alarm() is True


def test_a_machine_that_cannot_make_a_sound_admits_it() -> None:
    """A console reporting an audible alarm it cannot produce is worse than one
    that admits it is silent."""
    chime = Chime(player=None, clock=Clock())
    assert chime.available() is False
    assert chime.alarm() is False


# --------------------------------------------------- it never costs the alarm


def test_a_player_that_throws_costs_the_sound_and_nothing_else() -> None:
    """The alarm strip, the movement list and the recording all matter more than
    the noise. A sound device that has gone away - somebody unplugged a USB
    headset - must not reach the alarm path as an exception."""

    def angry() -> None:
        raise OSError("the sound device is gone")

    chime = Chime(player=angry, clock=Clock())
    assert chime.alarm() is False  # must not raise
    assert chime.played == 0


def test_a_failed_sound_does_not_start_the_quiet_period() -> None:
    """It was not played, so nothing has been said to the room, and the next
    alarm is still worth trying."""
    tries: list[int] = []

    def flaky() -> None:
        tries.append(1)
        raise OSError("not this time")

    clock = Clock()
    chime = Chime(player=flaky, clock=clock)
    chime.alarm()
    clock.tick(1.0)
    chime.alarm()
    assert len(tries) == 2


def test_the_real_player_never_waits_for_the_sound_to_finish() -> None:
    """A sound played synchronously on the thread that draws the window is a
    window that stops repainting for the length of the sound - during an alarm,
    which is the worst moment this console has.

    Checked by reading the flags rather than by timing, because a timing test
    for something this short is a flake on a busy machine.
    """
    import platform

    if platform.system() != "Windows":
        return

    import inspect

    from vmd.desktop import chime as module

    source = inspect.getsource(module._windows_player)
    assert "SND_ASYNC" in source
    assert "SND_NODEFAULT" in source, "a missing file would ding like an error"


def test_nothing_is_fetched_from_anywhere() -> None:
    """This laptop has no network. A console that wanted a sound file from
    somewhere would be a console that does not work."""
    import ast
    import inspect

    from vmd.desktop import chime as module

    # The imports rather than the text, because the text explains this rule and
    # would therefore fail a search for the words in it. What matters is what
    # the module can reach, and that is exactly what its imports say.
    tree = ast.parse(inspect.getsource(module))
    reached = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            reached.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            reached.add(node.module.split(".")[0])
    assert reached <= {"logging", "time", "winsound", "os", "__future__"}, reached
