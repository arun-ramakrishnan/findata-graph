# Probe: do std.utils.lock spin locks hold up as a mutex under concurrent
# TaskGroup tasks on Mojo 1.0?
#
# Verified result (2026-08-29): yes — 4 tasks x 100_000 lock-guarded
# increments on a plain shared Int produce exactly 400_000 (no lost
# updates). BlockingScopedLock is the RAII adapter over BlockingSpinLock
# (whose lock()/unlock() take an explicit owner token, usually an
# address; the raw API is easy to misuse — prefer the scoped form).
# Still NO channel/condvar anywhere in the stdlib; for producer/consumer
# you spin+sleep, the cryoluge recipe (forum.modular.com/t/2951).
# Full study: doc/local/mojo_concurrency.md.
#
# Run: .venv/bin/mojo run -I Mojo/src/common Mojo/src/common/spinlock_counter.mojo

from std.memory.alloc import alloc, Layout
from std.runtime.asyncrt import TaskGroup
from std.utils.lock import BlockingSpinLock, BlockingScopedLock


async def incr(
    counter: Pointer[Int, MutUntrackedOrigin],
    spin: Pointer[BlockingSpinLock, MutUntrackedOrigin],
    iters: Int,
) -> None:
    for _ in range(iters):
        with BlockingScopedLock(spin[]):
            counter.unsafe_offset(0)[] += 1


def main() raises:
    var counter = alloc(Layout[Int](count=1)).unsafe_leak()
    counter.unsafe_offset(0)[] = 0
    var spin = alloc(Layout[BlockingSpinLock](count=1)).unsafe_leak()
    spin.unsafe_offset(0)[] = BlockingSpinLock()

    var tg = TaskGroup()
    for _ in range(4):
        tg.create_task(incr(counter, spin, 100_000))
    tg.wait()
    print(
        "counter == 400000:", counter.unsafe_offset(0)[] == 400_000,
        " value:", counter.unsafe_offset(0)[],
    )
