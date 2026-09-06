"""How busy this machine is, right now, in the two numbers that decide the

argument the console keeps having with itself: is the picture late because the
CPU cannot decode two FHD streams in software, or for some other reason. Task
Manager answers it, but Task Manager is not on the screen the operator watches,
and the two consoles run where nobody is standing. So the band carries the same
two numbers Task Manager's front page does - processor load and memory load -
and turns the processor red when it is the wall.

Nothing is installed for this. `psutil` arrives only with the detector's extras
and a recording-only laptop does not have it, so the readings come from the same
kernel this file's neighbours already call through `ctypes` (see
`record_main.boot_time`). On anything that is not Windows, or if the kernel
refuses, `read()` returns None and the readout simply shows nothing - it is a
diagnostic aid, never a thing the console depends on.

The CPU number is a rate, not a level, so it needs two readings and the time
between them. `cpu_busy_percent` is that arithmetic on its own, pure and away
from the kernel call, so the one part with a decision in it can be checked
without a machine.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

# At or above this processor load, the readout goes red: this is the wall, and
# the picture being late is the CPU and not anything a bigger buffer will help.
# Below it there is headroom, and a late picture is jitter to be absorbed rather
# than decode that cannot keep up. 85 rather than 100 because a machine pegged at
# the wall rarely reads a clean hundred - it reads high nineties with the odd dip
# as the sampler happens to catch a gap - and the operator needs the colour to
# mean "no headroom left", which begins before the last percent is gone.
CPU_WALL_PERCENT = 85.0


@dataclass(frozen=True)
class Load:
    """The two readings, ready to draw. `cpu_percent` is None on the very first

    reading of a run - a rate needs a previous sample to measure against, and
    there is none yet - and the readout shows a dash until the second tick.
    """

    cpu_percent: float | None
    ram_percent: float
    ram_used_gb: float
    ram_total_gb: float

    @property
    def cpu_at_wall(self) -> bool:
        """Whether the processor is at or over the wall - what turns it red."""
        return self.cpu_percent is not None and self.cpu_percent >= CPU_WALL_PERCENT


def cpu_busy_percent(
    previous: tuple[int, int] | None, current: tuple[int, int]
) -> float | None:
    """Processor load across the gap between two `(idle, total)` readings.

    Each reading is a running total of hundred-nanosecond ticks the kernel has
    counted since it started: `total` is every tick, `idle` is the ones it spent
    doing nothing. What was busy in the gap is the busy ticks that accrued
    divided by all the ticks that accrued, so the answer is a load over that gap
    and not since boot.

    None when there is no previous reading to measure against, and None rather
    than a divide-by-zero when two readings land in the same tick - which a fast
    timer on a coarse clock can do. The caller keeps the last reading either way.
    """
    if previous is None:
        return None
    idle_prev, total_prev = previous
    idle_now, total_now = current
    total_delta = total_now - total_prev
    if total_delta <= 0:
        return None
    idle_delta = idle_now - idle_prev
    busy = total_delta - idle_delta
    percent = 100.0 * busy / total_delta
    # Clamp: the deltas can disagree by a tick at the edges and hand back a
    # hair under zero or over a hundred, and a load outside 0-100 is a wrong
    # answer however small.
    return max(0.0, min(100.0, percent))


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


_GIB = 1024**3


class SystemLoad:
    """One of these per readout. It holds the previous CPU reading, which is the

    only state there is - the memory reading needs none. `read()` on anything
    but Windows, or when the kernel will not answer, returns None every time and
    keeps no state, so the readout that owns it shows nothing and moves on.
    """

    def __init__(self) -> None:
        self._previous: tuple[int, int] | None = None
        self._kernel = None
        try:
            if hasattr(ctypes, "windll"):
                self._kernel = ctypes.windll.kernel32
        except Exception:  # noqa: BLE001 - no kernel is simply no readings
            self._kernel = None

    def _raw_cpu(self) -> tuple[int, int] | None:
        """`(idle, total)` in kernel ticks, or None if the call fails.

        `GetSystemTimes` hands back three FILETIMEs - idle, kernel, user - and
        the kernel figure already includes the idle one, so total is kernel plus
        user and every tick is counted once. Each FILETIME is eight bytes, which
        is a `c_ulonglong`, and passing one by reference gives the call the
        pointer it wants without a FILETIME struct in the middle.
        """
        if self._kernel is None:
            return None
        idle = ctypes.c_ulonglong()
        kernel = ctypes.c_ulonglong()
        user = ctypes.c_ulonglong()
        try:
            ok = self._kernel.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            )
        except Exception:  # noqa: BLE001 - a refused reading is not a failure
            return None
        if not ok:
            return None
        return idle.value, kernel.value + user.value

    def _raw_memory(self) -> tuple[float, float, float] | None:
        """`(percent, used_gib, total_gib)`, or None if the call fails."""
        if self._kernel is None:
            return None
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        try:
            ok = self._kernel.GlobalMemoryStatusEx(ctypes.byref(status))
        except Exception:  # noqa: BLE001
            return None
        if not ok:
            return None
        total = status.ullTotalPhys / _GIB
        used = (status.ullTotalPhys - status.ullAvailPhys) / _GIB
        return float(status.dwMemoryLoad), used, total

    def read(self) -> Load | None:
        """Both readings as one `Load`, or None when this machine will not give

        them. The CPU number is a rate against the last call, so the first call
        of a run has it as None; the memory number is a level and is there from
        the first call.
        """
        memory = self._raw_memory()
        if memory is None:
            return None
        current = self._raw_cpu()
        cpu = None
        if current is not None:
            cpu = cpu_busy_percent(self._previous, current)
            self._previous = current
        percent, used, total = memory
        return Load(
            cpu_percent=cpu,
            ram_percent=percent,
            ram_used_gb=used,
            ram_total_gb=total,
        )


def format_load(load: Load | None) -> str:
    """The one line the band draws. Empty when there is nothing to say, so the

    readout takes no room on a machine that cannot answer. `CPU --` while the
    rate waits for its second reading, then whole percents - a decimal place on
    a number that moves every second is noise nobody reads at two metres.
    """
    if load is None:
        return ""
    cpu = "--" if load.cpu_percent is None else f"{round(load.cpu_percent):d}%"
    ram = (
        f"{round(load.ram_percent):d}%  "
        f"{load.ram_used_gb:.1f}/{load.ram_total_gb:.1f} GB"
    )
    return f"CPU {cpu}   RAM {ram}"
