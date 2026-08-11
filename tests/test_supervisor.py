from vmd.supervisor import Managed, Supervisor


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeService:
    """Stands in for a recorder: start/stop plus a `running` flag."""

    def __init__(self, alive=True):
        self.running = alive
        self.starts = 0
        self.stops = 0

    def start(self):
        self.starts += 1
        self.running = True

    def stop(self):
        self.stops += 1
        self.running = False


def build(services, clock=None):
    clock = clock or FakeClock()
    managed = [Managed(name=name, service=service) for name, service in services.items()]
    return Supervisor(managed, clock=clock, restart_delay=2.0), clock


def test_first_tick_starts_everything():
    service = FakeService(alive=False)
    supervisor, _ = build({"recorder": service})
    assert supervisor.tick() == ["recorder"]
    assert service.starts == 1


def test_healthy_service_is_not_restarted():
    service = FakeService(alive=False)
    supervisor, clock = build({"recorder": service})
    supervisor.tick()
    clock.advance(10.0)
    assert supervisor.tick() == []
    assert service.starts == 1


def test_dead_service_is_restarted_after_the_delay():
    service = FakeService(alive=False)
    supervisor, clock = build({"recorder": service})
    supervisor.tick()
    service.running = False  # it died
    clock.advance(10.0)
    assert supervisor.tick() == ["recorder"]
    assert service.starts == 2


def test_restart_waits_for_the_delay():
    service = FakeService(alive=False)
    supervisor, clock = build({"recorder": service})
    supervisor.tick()
    service.running = False
    clock.advance(0.5)  # less than restart_delay
    assert supervisor.tick() == []
    assert service.starts == 1


def test_one_service_dying_does_not_touch_another():
    dying = FakeService(alive=False)
    healthy = FakeService(alive=False)
    supervisor, clock = build({"recorder": dying, "streamer": healthy})
    supervisor.tick()
    dying.running = False
    clock.advance(10.0)
    assert supervisor.tick() == ["recorder"]
    assert healthy.starts == 1


def test_restart_counts_are_tracked():
    service = FakeService(alive=False)
    supervisor, clock = build({"recorder": service})
    supervisor.tick()
    for _ in range(3):
        service.running = False
        clock.advance(10.0)
        supervisor.tick()
    assert supervisor.restarts["recorder"] == 3


def test_stop_all_stops_every_service():
    first = FakeService(alive=False)
    second = FakeService(alive=False)
    supervisor, _ = build({"a": first, "b": second})
    supervisor.tick()
    supervisor.stop_all()
    assert first.stops == 1
    assert second.stops == 1


def test_a_service_that_throws_on_start_does_not_break_the_tick():
    class Exploding(FakeService):
        def start(self):
            self.starts += 1
            raise RuntimeError("cannot start")

    exploding = Exploding(alive=False)
    healthy = FakeService(alive=False)
    supervisor, _ = build({"bad": exploding, "good": healthy})
    restarted = supervisor.tick()
    assert "good" in restarted
    assert healthy.starts == 1


def test_a_service_that_throws_on_stop_does_not_block_other_stops():
    # Shutdown must always complete. If one service's stop() could abort the loop,
    # the remaining recordings would never be closed cleanly.
    class Exploding(FakeService):
        def stop(self):
            self.stops += 1
            raise RuntimeError("cannot stop")

    exploding = Exploding(alive=True)
    healthy = FakeService(alive=True)
    supervisor, _ = build({"bad": exploding, "good": healthy})

    supervisor.stop_all()  # must not raise

    assert exploding.stops == 1
    assert healthy.stops == 1
    assert healthy.running is False


def test_a_child_started_elsewhere_still_counts_its_death_as_a_restart():
    """These are usually started directly, once, before the first tick.

    The console brings the recorder, the detector and go2rtc up itself and then
    hands them here to be kept alive. Counting only the starts this object had
    performed meant the first death of each was recorded as a first start, so
    `restarts` still read zero after every child had been killed and brought
    back - and that number is the one anyone looks at to find out whether
    something is flapping.
    """
    service = FakeService()
    service.start()  # the console starts it; the supervisor never saw it happen
    supervisor, clock = build({"recorder": service})

    supervisor.tick()  # sees it alive
    assert supervisor.restarts["recorder"] == 0

    service.running = False
    clock.advance(3.0)
    supervisor.tick()  # brings it back
    assert supervisor.restarts["recorder"] == 1

    service.running = False
    clock.advance(3.0)
    supervisor.tick()
    assert supervisor.restarts["recorder"] == 2


def test_a_child_that_was_never_alive_is_started_not_restarted():
    """A first start is not a restart, however many ticks it took to get there."""
    service = FakeService(alive=False)
    supervisor, _ = build({"recorder": service})
    supervisor.tick()
    assert supervisor.restarts["recorder"] == 0


# --------------------------------------------------------------------------
# Restarting is not recovering
# --------------------------------------------------------------------------


class DiesImmediately(FakeService):
    """Starts, and is dead again by the time anything looks.

    What the recorder did for a whole day: ffmpeg refused to write a header for
    a codec it cannot store, exited in milliseconds, and was started again five
    seconds later, twenty-four times, leaving twenty-four empty files.
    """

    def start(self):
        self.starts += 1
        self.running = False


def test_a_service_that_dies_as_fast_as_it_is_started_is_not_called_running():
    """`start()` returning is not the child working.

    The supervisor counted twenty-four restarts and concluded nothing from
    them, because nothing here distinguished a child that came back and worked
    from one that came back and died again. Both are a name in the list this
    returns, and the console read that list as "started".
    """
    service = DiesImmediately(alive=False)
    supervisor, clock = build({"recorder": service})
    for _ in range(24):
        supervisor.tick()
        clock.advance(5.0)

    assert service.starts == 24
    health = supervisor.health()["recorder"]
    assert health["settled"] is False
    assert health["short_lived"] >= 20
    assert health["flapping"] is True
    assert "never stayed up" in health["reason"], health["reason"]


def test_a_service_that_comes_back_and_works_is_reported_as_recovered():
    """The other half of the same question, or the reading means nothing."""
    service = FakeService(alive=False)
    supervisor, clock = build({"recorder": service})
    supervisor.tick()
    service.running = False  # one death
    clock.advance(5.0)
    supervisor.tick()  # and it comes back
    clock.advance(600.0)  # and stays up
    supervisor.tick()

    health = supervisor.health()["recorder"]
    assert health["settled"] is True
    assert health["flapping"] is False
    assert health["restarts"] == 1
    assert health["reason"] == ""


def test_a_service_that_settles_after_flapping_stops_being_called_flapping():
    """A camera that was off for ten minutes must not be a permanent verdict."""
    service = DiesImmediately(alive=False)
    supervisor, clock = build({"recorder": service})
    for _ in range(5):
        supervisor.tick()
        clock.advance(5.0)
    assert supervisor.health()["recorder"]["flapping"] is True

    service.start = lambda: setattr(service, "running", True)
    supervisor.tick()
    clock.advance(600.0)
    supervisor.tick()
    health = supervisor.health()["recorder"]
    assert health["flapping"] is False
    assert health["settled"] is True


def test_the_flapping_is_said_out_loud_and_not_once_a_tick(caplog):
    """This process runs for months; a warning every two seconds is silence."""
    service = DiesImmediately(alive=False)
    supervisor, clock = build({"recorder": service})
    with caplog.at_level("WARNING", logger="vmd.supervisor"):
        for _ in range(30):
            supervisor.tick()
            clock.advance(5.0)
    said = [r.getMessage() for r in caplog.records if "never stayed up" in r.getMessage()]
    assert said, "nothing ever concluded anything from twenty-four restarts"
    assert len(said) <= 3, said


def test_a_service_started_before_the_supervisor_saw_it_can_still_flap():
    """The console starts these itself and hands them over to be kept alive."""
    service = DiesImmediately(alive=True)
    supervisor, clock = build({"recorder": service})
    supervisor.tick()  # sees it alive, having started it itself
    for _ in range(6):
        service.running = False
        clock.advance(5.0)
        supervisor.tick()
    assert supervisor.health()["recorder"]["flapping"] is True
