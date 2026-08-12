"""Probe: why does test_browser_opens_only_once_the_port_accepts fail under load?

Observed once on the demo machine, 2026-08-12 (43n demo round 1), inside an
87.85 s full-suite run:

    AssertionError: the poll thread never noticed the listen()

The poll thread was still alive after `thread.join(timeout=20)`. It passed on the
same platform at 43j's round-3 pin, so it is load-dependent.

WHY NOT JUST LOOP THE TEST. The failure appeared inside a loaded full-suite run.
Running that one test alone removes the very condition that produced it, so a
green loop would prove nothing -- it is not the same experiment. This probe keeps
the load and, more importantly, reports the ONE NUMBER that separates the two
live hypotheses instead of only saying "failed":

  H1  BUDGET EXHAUSTED. `_open_when_ready`'s deadline is spent in *virtual*
      seconds -- the test advances `elapsed` only inside its own `sleep` shim --
      while `join()` waits in *wall* seconds. Under load, wall time per poll far
      exceeds virtual time per poll, so the worker can still be legitimately
      polling when the wall-clock join expires. If this is it, `elapsed` at the
      end sits at or near the 15.0 s deadline.

  H2  STUCK IN CONNECT. The worker is blocked in `socket.create_connection`
      against a socket nothing ever `accept()`s -- note the test never accepts,
      and `listen(1)` gives a backlog of one. If this is it, `elapsed` at the end
      is still small: the worker never got far enough to spend its budget.

They are different bugs with different fixes, which is why this probe exists
rather than a bare pass/fail count.

Run from the repo root; no Micro-Manager and no microscope needed.

    python design/35-webserve-flake-probe.py --iterations 40 --load 8

On Windows use PowerShell and redirect:

    python design\35-webserve-flake-probe.py --iterations 40 --load 8 > webserve-probe.txt 2>&1
    Write-Host "exit code (expected 0):" $LASTEXITCODE
    Get-Content webserve-probe.txt

--load N spawns N busy threads to emulate a loaded suite. Start with the machine
otherwise idle at --load 0, then raise it until the failure appears; the load
level at which it first appears is itself a reportable finding.

macOS CALIBRATION (2026-08-12, 6 rounds at --load 0 and 6 at --load 8): all OK,
and `e@listen` read 0.00 every time. That is not a rounding artifact -- it means
the worker made ZERO poll iterations before listen(): its first
`create_connection` was still in flight for the whole 0.3 s wait, so the `sleep`
shim was never called and the virtual clock never advanced. The in-flight connect
then completed once listen() landed.

So on macOS the pre-listen phase is one blocked connect, not a polling loop. If
Windows shows `e@listen` climbing instead, the worker IS looping there, the two
platforms are running different code paths through the same test, and that
difference is very likely the whole finding. Compare that column first.
"""
import argparse
import socket
import threading
import time

from microclaw import webserve

WORKER_TIMEOUT = 15.0        # _open_when_ready's own default deadline


def _burn(stop):
    """Emulate a loaded suite: contend for the GIL and the scheduler."""
    x = 0
    while not stop.is_set():
        for _ in range(50_000):
            x += 1


def one_round(join_timeout, pre_listen_wall):
    opened = []
    real_open = webserve.webbrowser.open
    webserve.webbrowser.open = opened.append
    elapsed = 0.0
    lock = threading.Lock()

    def clock():
        with lock:
            return elapsed

    def worker_sleep(seconds):
        # Exactly the test's shim, including the real sleep.
        nonlocal elapsed
        time.sleep(seconds)
        with lock:
            elapsed += seconds

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    try:
        thread = webserve._open_when_ready(
            "127.0.0.1", port, f"http://127.0.0.1:{port}",
            clock=clock, sleep=worker_sleep,
        )
        time.sleep(pre_listen_wall)
        opened_early = list(opened)
        elapsed_at_listen = clock()
        sock.listen(1)
        t_listen = time.monotonic()
        thread.join(timeout=join_timeout)
        wall_after_listen = time.monotonic() - t_listen
        alive = thread.is_alive()
        elapsed_end = clock()
    finally:
        sock.close()
        webserve.webbrowser.open = real_open

    if opened_early:
        verdict = "OPENED_TOO_EARLY"          # a different bug entirely
    elif not alive and opened:
        verdict = "OK"
    elif not alive and not opened:
        verdict = "H1_BUDGET_EXHAUSTED"       # worker gave up before listen()
    elif elapsed_end >= WORKER_TIMEOUT * 0.9:
        verdict = "H1_BUDGET_EXHAUSTED"       # still alive, but budget spent
    else:
        verdict = "H2_STUCK_IN_CONNECT"       # alive with budget to spare
    return {
        "verdict": verdict,
        "elapsed_at_listen": round(elapsed_at_listen, 2),
        "elapsed_end": round(elapsed_end, 2),
        "wall_after_listen": round(wall_after_listen, 2),
        "alive": alive,
        "opened": bool(opened),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=40)
    ap.add_argument("--load", type=int, default=0,
                    help="background busy threads emulating suite load")
    ap.add_argument("--join-timeout", type=float, default=20.0)
    ap.add_argument("--pre-listen-wall", type=float, default=0.3)
    args = ap.parse_args()

    stop = threading.Event()
    burners = [threading.Thread(target=_burn, args=(stop,), daemon=True)
               for _ in range(args.load)]
    for b in burners:
        b.start()

    print(f"iterations={args.iterations} load={args.load} "
          f"join_timeout={args.join_timeout} worker_deadline={WORKER_TIMEOUT}")
    print(f"{'#':>4} {'verdict':<22} {'e@listen':>9} {'e@end':>7} "
          f"{'wall_after':>11} {'alive':>6} {'opened':>7}")
    counts = {}
    try:
        for i in range(1, args.iterations + 1):
            r = one_round(args.join_timeout, args.pre_listen_wall)
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
            print(f"{i:>4} {r['verdict']:<22} {r['elapsed_at_listen']:>9} "
                  f"{r['elapsed_end']:>7} {r['wall_after_listen']:>11} "
                  f"{str(r['alive']):>6} {str(r['opened']):>7}")
    finally:
        stop.set()

    print("\nsummary:")
    for verdict, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {verdict:<22} {n:>4} / {args.iterations}")
    failures = args.iterations - counts.get("OK", 0)
    print(f"\nnon-OK rounds: {failures} / {args.iterations}")
    print("Report this whole table. If every round is OK at high --load, say so:")
    print("a probe that cannot reproduce the failure is a real result, and means")
    print("the trigger is something this probe does not model (test ordering,")
    print("port reuse, or another test's leaked threads) -- not that it is fixed.")


if __name__ == "__main__":
    main()
