"""
 Probe: does std.runtime.asyncrt.TaskGroup deliver real multicore on Mojo 1.0?

 Verified result (2026-08-29, 4 logical cores): 4 CPU-bound tasks via
 TaskGroup fan-out run 2.89x faster than the sequential loop (15.6 ms ->
 5.4 ms); results come back through shared heap slots, tg.wait() is the
 barrier. Two API contracts pinned here:
   * create_task() takes a NON-raising async def coroutine — a `raises`
     coroutine is a RaisingCoroutine and is rejected.
   * group tasks return None (no futures): write into Pointer/Atomic
     slots and read after wait().
 Full study: doc/local/mojo_concurrency.md.

 Run: .venv/bin/mojo run -I Mojo/src/common Mojo/src/common/taskgroup_fanout.mojo
"""


from std.memory.alloc import alloc, Layout
from std.runtime.asyncrt import TaskGroup
from std.sys.info import num_logical_cores
from std.time import perf_counter_ns


def cpu_work(n: Int) -> Int:
    var acc: Int = 0
    for i in range(n):
        acc += (i * 2654435761) % 97
    return acc


async def work(res: Pointer[Int, MutUntrackedOrigin], idx: Int, n: Int) -> None:
    res.unsafe_offset(idx)[] = cpu_work(n)


def main() raises:
    print("logical cores:", num_logical_cores())

    var res = alloc(Layout[Int](count=4)).unsafe_leak()
    for i in range(4):
        res.unsafe_offset(i)[] = -1

    # Sequential baseline.
    var t0 = perf_counter_ns()
    var seq: Int = 0
    for _ in range(4):
        seq += cpu_work(3_000_000)
    var seq_ns = perf_counter_ns() - t0

    # TaskGroup fan-out.
    var tg = TaskGroup()
    t0 = perf_counter_ns()
    for i in range(4):
        tg.create_task(work(res, i, 3_000_000))
    tg.wait()
    var par_ns = perf_counter_ns() - t0

    var total: Int = 0
    for i in range(4):
        if res.unsafe_offset(i)[] == -1:
            print("slot", i, "NEVER WROTE")
        total += res.unsafe_offset(i)[]
    print("seq == par:", seq == total, " sum:", total)
    print(
        "sequential:",
        Float64(seq_ns) / 1e9,
        "s   taskgroup:",
        Float64(par_ns) / 1e9,
        "s   speedup:",
        Float64(seq_ns) / Float64(par_ns),
    )
