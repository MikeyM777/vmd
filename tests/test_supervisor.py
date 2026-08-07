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
