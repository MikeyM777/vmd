"""What every test in this suite gets, whether it asks for it or not.

There was no `conftest.py` in this repo at all, which is why two things that
should have been settled once had never been settled anywhere:

**A test may fail. A test may not hang.** Three separate mutations have wedged
this suite instead of failing it, and each time the fix was to that one mutation
- never to the reason a test *can* hang. The suite is run by hand on an
always-on laptop, sometimes overnight; a run that stops has told nobody
anything, and there is no CI to notice. `pytest-timeout` is configured in
`pyproject.toml` at 30 seconds, which is roughly ten times the slowest honest
unit test, and raised here for the integration tests, which legitimately spawn
go2rtc and ffmpeg and record real clips in real time.

**No test reaches out of this machine.** The console under test posts camera and
radio passwords at whatever address it is given, and the tests hand it addresses
by the dozen. A typo that turns one of them into a real host is a password on
somebody else's wire, and on the air-gapped laptop this ships to it is traffic
that should not exist at all. So loopback is allowed, the documented test
networks are allowed, and anything else fails the test that tried it - loudly,
naming the address, rather than timing out in six seconds and looking like a
flake.
"""

from __future__ import annotations

import ipaddress
import socket

import pytest

# The integration tests are slow for honest reasons: the acceptance test runs
# real go2rtc, a real recorder child process and a real ffmpeg against a
# synthetic camera, and three recorder tests record twelve-second clips at real
# time because `-re` is the point of them. Measured at ~32 s for the worst one,
# so this is four times the worst honest case and still bounds a wedge.
INTEGRATION_TIMEOUT = 120.0

# RFC 5737 reserves these three for documentation. They are guaranteed never to
# be routed, so a connection to one is guaranteed never to be answered - which
# is precisely what a test of "the radio is unreachable" wants, and why the
# suite uses 192.0.2.99. Allowed here for that reason and no other.
DOCUMENTATION_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),      # TEST-NET-1
    ipaddress.ip_network("198.51.100.0/24"),   # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),    # TEST-NET-3
)


def pytest_collection_modifyitems(config, items) -> None:
    """Give the integration tests their own ceiling, and leave the rest at 30 s.

    Applied here rather than by hand on each test so that a new integration test
    cannot be written without one, which is how the last gap of this shape
    happened.
    """
    for item in items:
        if item.get_closest_marker("integration") and not item.get_closest_marker("timeout"):
            item.add_marker(pytest.mark.timeout(INTEGRATION_TIMEOUT))


def pytest_timeout_set_timer(item, settings):
    """The watchdog thread, named after the harness rather than after the test.

    `pytest-timeout`'s own name for it is "pytest_timeout <nodeid>", which puts
    the name of the test file into `threading.enumerate()` - and this suite has
    tests that read that list to check the threads the code under test created.
    `tests/test_detect_classify.py::test_the_worker_does_not_hold_the_process_open`
    asks whether every thread whose name contains "classify" is a daemon, and a
    watchdog called "pytest_timeout tests/test_detect_classify.py::..." is not
    one - so switching the timeout on failed a test about somebody else's
    threads. A test harness that changes what the tests can see is a harness
    that has to be corrected, not a defect in the test.

    Only the thread method is taken over; anything else falls through to the
    plugin, which is also what happens if this stops matching its internals.
    """
    if settings.method != "thread":
        return None
    import threading as _threading

    from pytest_timeout import timeout_timer

    timer = _threading.Timer(settings.timeout, timeout_timer, (item, settings))
    timer.name = "vmd-test-watchdog"

    def cancel() -> None:
        timer.cancel()
        timer.join()

    item.cancel_timeout = cancel
    timer.start()
    return True


def _is_allowed(address) -> bool:
    """Whether a socket may go there: this machine, or a black hole.

    Anything that is not an ordinary IP address - an AF_UNIX path, a Bluetooth
    tuple, whatever a future Qt uses - is left alone. This is a guard against
    one specific mistake, not a sandbox, and a guard that breaks unrelated
    things gets switched off, which would leave nothing.
    """
    if not isinstance(address, tuple) or not address:
        return True
    host = address[0]
    if not isinstance(host, str) or not host:
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]").split("%")[0])
    except ValueError:
        # A name, not an address. Resolving it here would be the very lookup
        # this is meant to prevent, so it is refused on the name.
        return host in ("localhost", "localhost.localdomain")
    if ip.is_loopback or ip.is_unspecified:
        return True
    return any(ip in network for network in DOCUMENTATION_NETWORKS)


@pytest.fixture(autouse=True, scope="session")
def _no_test_leaves_this_machine():
    """Fail the test that opens a socket to a real, routable address.

    Not a firewall - a subprocess is not affected and is not meant to be, since
    go2rtc and ffmpeg are started by the integration tests on purpose and talk
    to each other over loopback. This catches the case that matters: code under
    test, in this process, handed an address by a test that meant to use a fake
    one.
    """
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def guarded(self, address, *args, **kwargs):
        if not _is_allowed(address):
            raise AssertionError(
                f"a test tried to open a socket to {address!r}, which is a real "
                "address. Tests reach loopback or a documented test network "
                "(192.0.2.x) and nothing else: this console posts passwords at "
                "whatever address it is given."
            )
        return connect(self, address, *args, **kwargs)

    def guarded_ex(self, address, *args, **kwargs):
        if not _is_allowed(address):
            raise AssertionError(
                f"a test tried to open a socket to {address!r}, which is a real address"
            )
        return connect_ex(self, address, *args, **kwargs)

    socket.socket.connect = guarded
    socket.socket.connect_ex = guarded_ex
    try:
        yield
    finally:
        socket.socket.connect = connect
        socket.socket.connect_ex = connect_ex
