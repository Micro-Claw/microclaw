"""design/27 — spike: what does `return None` from a hook actually do?

hook_docs.py promises three return-None behaviors: post_hardware "skips image
capture for this event entirely", pre_hardware "skips this event entirely
(hardware never moves)", and image_process_fn "discards this image and drops
all remaining events for that position". The rig (2026-07-15, demo config,
history 20260715_164106) showed the first is false in the worst direction:
five "skipped" tiles produced five extra frames with no position identity —
ghost exposures. This spike measures every link of that chain and the two
sibling promises.

B_1 reads pyjavaz's wire encoding of None off a real socket: _DataSocket.send
has `if message is None: message = {}`, so None leaves the process as an empty
JSON object — the Java side can never see a null.

B_2 drives pycro-manager's REAL hook thread (_run_acq_hook) over real ZMQ
sockets, playing the Java side: a passthrough return arrives intact, a None
return arrives as {}, and a RAISING hook calls acquisition.abort(e) and STILL
sends {} — so even the loud path may race one ghost event out the door.

B_3–B_7 are gated on a Micro-Manager at localhost:4827 (demo config is
enough) and measure the engine's half for real:

  B_3  post_hardware returns None mid-run: the "skipped" events still fire
       the camera (unlabeled ghost frames), the stage does not move for them,
       and the run completes without error — the rig trace, deterministic.
  B_4  pre_hardware returns None: "hardware never moves" is the true half;
       the fired capture is the false half.
  B_5  image_process_fn returns None for one position: the camera still fires
       every remaining event for that position (nothing is dropped); only the
       dataset is filtered.
  B_6  the honest lever: a hook that RAISES aborts the whole acquisition and
       the error surfaces to the caller. Measures how many ghost exposures
       the abort race lets out.
  B_7  the empty-list probe ([] -> {"events": []} -> Java sequence.get(0) on
       an empty list): measures whether that fails loud, silent, or wedges.
       Runs LAST and watchdog-wrapped; informational — it fails the spike
       only by being silent.

B_1/B_2 need only pycro-manager + pyjavaz importable: no hardware, no MM.
B_3–B_7 write datasets to temp dirs they delete, and restore the stage XY
they found. Exits non-zero if any stage that ran fails its verdict (skipped
gated stages are not failures).

Run:  python design/27-return-none-spike.py
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback

import zmq

SEP = "=" * 78
_ENC = "iso-8859-1"          # pyjavaz's wire encoding
_B7_WEDGED = False


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# B_1 — pyjavaz has no encoding for None
# ---------------------------------------------------------------------------

def b1_pyjavaz_sends_none_as_empty_object() -> bool:
    """_DataSocket.send (pyjavaz/bridge.py:104): `if message is None: message
    = {}`. Measured off the wire, not read off the page: a real PushSocket, a
    raw PULL on the other end, and the bytes in between."""
    print("\nB_1  pyjavaz wire encoding of None")
    from pyjavaz import PushSocket

    port = _free_port()
    ctx = zmq.Context.instance()
    pull = ctx.socket(zmq.PULL)
    pull.setsockopt(zmq.RCVTIMEO, 5000)
    pull.setsockopt(zmq.LINGER, 0)
    pull.connect(f"tcp://127.0.0.1:{port}")
    ps = PushSocket(port)                    # pyjavaz PUSH sockets bind
    try:
        ps.send(None)
        raw_none = pull.recv_multipart()[0]
        event = {"axes": {"position": "t0"}, "x": 1.0}
        ps.send(event)
        raw_event = pull.recv_multipart()[0]
    finally:
        ps._socket.close(0)
        pull.close(0)

    print(f"     send(None)  on the wire : {raw_none!r}")
    print(f"     send(event) on the wire : {raw_event!r}")
    ok = raw_none == b"{}" and json.loads(raw_event.decode(_ENC)) == event
    print("     VERDICT: None leaves the process as an EMPTY JSON OBJECT — the"
          "\n              Java side can never receive a null from a Python hook."
          if ok else "     VERDICT: encoding differs — re-derive the chain.")
    return ok


# ---------------------------------------------------------------------------
# B_2 — pycro-manager's real hook thread, over real sockets
# ---------------------------------------------------------------------------

def b2_run_acq_hook_marshalling() -> bool:
    """Drive the REAL _run_acq_hook (java_backend_acquisitions.py:63) and play
    the Java side: we push events at its PullSocket and read its PushSocket.

    Three returns, three wire truths:
      - a dict passes through intact (the normal case);
      - None is sent as {} (B_1's encoding, through the real hook loop);
      - a RAISE calls acquisition.abort(e) and STILL sends {} — the exception
        path reuses the None path (lines 90-92), so even an aborting hook
        hands the engine one more ghost event.
    """
    print("\nB_2  the real _run_acq_hook: passthrough, None, and raise")
    from pycromanager.acquisition.java_backend_acquisitions import _run_acq_hook

    pull_port, push_port = _free_port(), _free_port()
    ctx = zmq.Context.instance()
    # _run_acq_hook makes PushSocket(pull_port) [binds] + PullSocket(push_port)
    # [connects]; the Java side is therefore PUSH-bind on push_port and
    # PULL-connect on pull_port. We are the Java side.
    events_out = ctx.socket(zmq.PUSH)
    events_out.setsockopt(zmq.LINGER, 0)
    events_out.bind(f"tcp://127.0.0.1:{push_port}")
    replies_in = ctx.socket(zmq.PULL)
    replies_in.setsockopt(zmq.RCVTIMEO, 10000)
    replies_in.setsockopt(zmq.LINGER, 0)
    replies_in.connect(f"tcp://127.0.0.1:{pull_port}")

    class FakeAcq:
        def __init__(self):
            self.aborts = []

        def abort(self, e):
            self.aborts.append(e)

    fake = FakeAcq()
    connected = threading.Event()

    def hook_fn(event):
        label = (event.get("axes") or {}).get("position")
        if label == "skipme":
            return None                       # what hook_docs says skips
        if label == "boom":
            raise ValueError("hook wants to stop the acquisition")
        return event

    t = threading.Thread(
        target=_run_acq_hook,
        args=(fake, pull_port, push_port, connected, queue.Queue(), hook_fn),
        daemon=True, name="B2-real-acq-hook",
    )
    t.start()
    if not connected.wait(timeout=5.0):
        print("     VERDICT: hook thread never connected — environment problem.")
        return False

    def exchange(msg: dict) -> dict:
        events_out.send(json.dumps(msg).encode(_ENC))
        return json.loads(replies_in.recv_multipart()[0].decode(_ENC))

    try:
        event = {"axes": {"position": "keep"}, "x": 5.0, "y": 0.0}
        passed_through = exchange(event)
        none_reply = exchange({"axes": {"position": "skipme"}, "x": 6.0})
        raise_reply = exchange({"axes": {"position": "boom"}, "x": 7.0})
        exchange({"special": "acquisition-end"})   # echoed; ends the thread
    finally:
        t.join(timeout=5.0)
        events_out.close(0)
        replies_in.close(0)

    print(f"     dict return  -> engine receives : {passed_through}")
    print(f"     None return  -> engine receives : {none_reply}")
    print(f"     raise        -> engine receives : {raise_reply}"
          f"   abort(e) called: {len(fake.aborts)}x"
          f" ({type(fake.aborts[0]).__name__ if fake.aborts else '-'})")
    ok = (passed_through == event and none_reply == {} and raise_reply == {}
          and len(fake.aborts) == 1 and isinstance(fake.aborts[0], ValueError)
          and not t.is_alive())
    print("     VERDICT: the engine NEVER sees a cancel — None and raise both"
          "\n              deliver an empty event; raise at least aborts alongside it."
          if ok else "     VERDICT: marshalling differs — re-derive the chain.")
    return ok


# ---------------------------------------------------------------------------
# B_3–B_7 — the engine's half, gated on a live Micro-Manager (demo config)
# ---------------------------------------------------------------------------

def _mm_reachable() -> bool:
    try:
        socket.create_connection(("127.0.0.1", 4827), timeout=1.0).close()
        return True
    except OSError:
        return False


def _labeled_events(n: int = 6) -> list[dict]:
    return [{"axes": {"position": f"t{i}"}, "x": 10.0 * i, "y": 0.0}
            for i in range(n)]


def _make_processor(record: list, reject: set | None = None):
    """2-arg image_process_fn that records every frame's axes. A frame whose
    axes carry no 'position' key is a GHOST — an exposure no submitted event
    asked for by that identity."""

    def proc(image, metadata):
        axes = dict(metadata.get("Axes") or {})
        record.append(axes)
        if reject and axes.get("position") in reject:
            return None
        return image, metadata

    return proc


def _split_ghosts(record: list) -> tuple[list, int]:
    labels = [a["position"] for a in record if "position" in a]
    ghosts = sum(1 for a in record if "position" not in a)
    return labels, ghosts


def _dataset_positions(acq) -> tuple[set, int, int]:
    """(position labels present, total stored frames, stored ghost frames)."""
    dataset = acq.get_dataset()
    try:
        coords = dataset.get_image_coordinates_list()
        labels = {c["position"] for c in coords if "position" in c}
        ghosts = sum(1 for c in coords if "position" not in c)
        return labels, len(coords), ghosts
    finally:
        try:
            dataset.close()
        except Exception:
            pass


def _wait_processed(record: list, n: int, timeout_s: float = 10.0) -> None:
    """The image processor drains asynchronously after __exit__; give it a
    beat so `record` is complete before we assert on it."""
    deadline = time.monotonic() + timeout_s
    while len(record) < n and time.monotonic() < deadline:
        time.sleep(0.05)


def b3_post_hardware_none(core, save_dir: str) -> bool:
    """The rig trace, deterministic: skip t3..t5 from post_hardware."""
    print("\nB_3  post_hardware returns None for t3..t5 — the founding trace, replayed")
    from pycromanager import Acquisition

    skip = {"t3", "t4", "t5"}
    record: list = []

    def post_hw(event):
        if (event.get("axes") or {}).get("position") in skip:
            return None                       # hook_docs.py:46's promise
        return event

    with Acquisition(directory=save_dir, name="b3", show_display=False,
                     image_process_fn=_make_processor(record),
                     post_hardware_hook_fn=post_hw) as acq:
        acq.acquire(_labeled_events(6))
    x_after = core.get_x_position()
    _wait_processed(record, 6)

    labels, ghosts = _split_ghosts(record)
    ds_labels, ds_frames, ds_ghosts = _dataset_positions(acq)
    print(f"     labeled frames processed  : {labels}")
    print(f"     GHOST frames processed    : {ghosts}   (skipped events that still exposed)")
    print(f"     stage X after run         : {x_after:.1f} um   (t2's X is 20.0 —"
          f" ghosts carry no coords, so no move)")
    print(f"     dataset                   : {ds_frames} frames, labels {sorted(ds_labels)},"
          f" ghost frames stored: {ds_ghosts}")
    ok = (labels == ["t0", "t1", "t2"] and ghosts == 3
          and abs(x_after - 20.0) < 1.0 and ds_labels == {"t0", "t1", "t2"})
    print("     VERDICT: 'skip image capture entirely' fires the camera anyway —"
          "\n              one unlabeled ghost exposure per skipped event, no error."
          if ok else "     VERDICT: engine behaved differently — see numbers above.")
    return ok


def b4_pre_hardware_none(core, save_dir: str) -> bool:
    """Same skip from pre_hardware: the '(hardware never moves)' half is
    accidentally true; the capture still fires."""
    print("\nB_4  pre_hardware returns None for t3..t5")
    from pycromanager import Acquisition

    skip = {"t3", "t4", "t5"}
    record: list = []

    def pre_hw(event):
        if (event.get("axes") or {}).get("position") in skip:
            return None                       # hook_docs.py:56's promise
        return event

    with Acquisition(directory=save_dir, name="b4", show_display=False,
                     image_process_fn=_make_processor(record),
                     pre_hardware_hook_fn=pre_hw) as acq:
        acq.acquire(_labeled_events(6))
    x_after = core.get_x_position()
    _wait_processed(record, 6)

    labels, ghosts = _split_ghosts(record)
    print(f"     labeled frames processed  : {labels}")
    print(f"     GHOST frames processed    : {ghosts}")
    print(f"     stage X after run         : {x_after:.1f} um   (never moved past t2: TRUE half)")
    ok = labels == ["t0", "t1", "t2"] and ghosts == 3 and abs(x_after - 20.0) < 1.0
    print("     VERDICT: hardware indeed never moves — and the camera fires anyway."
          "\n              Half-true is still a ghost exposure per skipped event."
          if ok else "     VERDICT: engine behaved differently — see numbers above.")
    return ok


def b5_image_process_none(core, save_dir: str) -> bool:
    """image_process_fn returns None for every t0 frame. hook_docs.py:23-24
    claims this 'drops all remaining events for that position'. The processor
    is strictly downstream of the engine (java_backend_acquisitions.py:176:
    `if processed is None: continue`) — there is nothing to drop WITH."""
    print("\nB_5  image_process_fn returns None for position t0 (3 frames each of t0, t1)")
    from pycromanager import Acquisition

    events = [{"axes": {"position": p, "time": k}, "x": x, "y": 0.0}
              for (p, x) in (("t0", 0.0), ("t1", 10.0)) for k in range(3)]
    record: list = []

    with Acquisition(directory=save_dir, name="b5", show_display=False,
                     image_process_fn=_make_processor(record, reject={"t0"})) as acq:
        acq.acquire(events)
    _wait_processed(record, 6)

    labels, ghosts = _split_ghosts(record)
    per_label = {p: labels.count(p) for p in ("t0", "t1")}
    ds_labels, ds_frames, _ = _dataset_positions(acq)
    print(f"     frames the camera fired   : {per_label}   (drop would leave t0 at 1)")
    print(f"     dataset positions         : {sorted(ds_labels)} with {ds_frames} frames")
    ok = (per_label == {"t0": 3, "t1": 3} and ghosts == 0
          and ds_labels == {"t1"} and ds_frames == 3)
    print("     VERDICT: the discard is real, the drop is fiction — every t0 event"
          "\n              still moved, exposed, and was then thrown away."
          if ok else "     VERDICT: engine behaved differently — see numbers above.")
    return ok


def b6_raise_aborts_loudly(core, save_dir: str) -> bool:
    """The one lever that works: a raising hook -> acquisition.abort(e) ->
    the error surfaces at __exit__. Also measures the race B_2 exposed: the
    exception path still sends {} (java_backend_acquisitions.py:92), so the
    abort may let ghost exposures out before it lands."""
    print("\nB_6  a hook that RAISES at t3 — the honest 'stop' (Fix 1's replacement lever)")
    from pycromanager import Acquisition

    record: list = []

    def post_hw(event):
        if (event.get("axes") or {}).get("position") == "t3":
            raise RuntimeError("B_6: guard says stop")
        return event

    caught = None
    t0 = time.monotonic()
    try:
        with Acquisition(directory=save_dir, name="b6", show_display=False,
                         image_process_fn=_make_processor(record),
                         post_hardware_hook_fn=post_hw) as acq:
            acq.acquire(_labeled_events(6))
    except Exception as e:
        caught = e
    elapsed = time.monotonic() - t0
    time.sleep(2.0)                     # let any racing frames drain

    labels, ghosts = _split_ghosts(record)
    surfaced = f"{type(caught).__name__}: {caught}" if caught else "NO"
    print(f"     exception surfaced        : {surfaced}")
    print(f"     labeled frames processed  : {labels}")
    print(f"     ghost exposures the abort race let out: {ghosts}")
    print(f"     __exit__ returned after   : {elapsed:.1f}s")
    ok = caught is not None and "t4" not in labels and "t5" not in labels
    print("     VERDICT: raising is loud and it stops — the run ends with an error"
          "\n              the caller cannot miss. (Ghost count above is the race's price.)"
          if ok else "     VERDICT: the raise did not stop/surface — Fix 2 needs a rethink.")
    return ok


def b7_empty_list_probe(core, save_dir: str) -> bool:
    """A hook returning [] reaches Java as {"events": []}; the deserializer
    builds AcquisitionEvent(new empty list), whose first line is
    sequence.get(0) — IndexOutOfBoundsException. Loud, silent, or wedge?
    Informational; fails only by being SILENT (which would falsify the source
    reading). Watchdog-wrapped: the expected failure is on the Java side and
    the Python __exit__ may never hear about it."""
    global _B7_WEDGED
    print("\nB_7  the empty-list probe — [] as a candidate cancel encoding")
    from pycromanager import Acquisition

    record: list = []
    outcome: dict = {}

    def post_hw(event):
        if (event.get("axes") or {}).get("position") == "t3":
            return []
        return event

    def run():
        try:
            with Acquisition(directory=save_dir, name="b7", show_display=False,
                             image_process_fn=_make_processor(record),
                             post_hardware_hook_fn=post_hw) as acq:
                acq.acquire(_labeled_events(6))
            outcome["acq"] = acq
        except Exception as e:
            outcome["error"] = e

    worker = threading.Thread(target=run, daemon=True, name="B7-probe")
    worker.start()
    worker.join(timeout=45.0)

    labels, ghosts = _split_ghosts(record)
    if worker.is_alive():
        _B7_WEDGED = True
        print("     outcome: WEDGED — __exit__ never returned within 45 s."
              "\n     (A design/24-A_10-shaped hang; the spike will force-exit.)")
        print(f"     labeled frames processed before the wedge: {labels}")
        return True                     # informational: a wedge is an answer
    if "error" in outcome:
        print(f"     outcome: LOUD — {type(outcome['error']).__name__}: {outcome['error']}")
        print(f"     labeled frames processed : {labels}   ghosts: {ghosts}")
        return True
    silent_skip = set(labels) == {"t0", "t1", "t2", "t4", "t5"} and ghosts == 0
    print(f"     outcome: completed without error")
    print(f"     labeled frames processed : {labels}   ghosts: {ghosts}")
    if silent_skip:
        print("     VERDICT: [] SILENTLY SKIPPED t3 — that contradicts the source"
              "\n              reading (sequence.get(0) on an empty list) — re-derive!")
        return False
    print("     VERDICT: not a usable cancel encoding (and not silent).")
    return True


def run_gated_stages() -> dict[str, bool] | None:
    print("\nB_3..B_7  the real engine (Micro-Manager, demo config is enough) — gated")
    if not _mm_reachable():
        print("     SKIPPED — no Micro-Manager on localhost:4827."
              "\n     Run this spike on a machine with MM open (demo config suffices)"
              "\n     to measure the engine-side half of every verdict above.")
        return None

    from pycromanager import Core

    core = Core()
    x0, y0 = core.get_x_position(), core.get_y_position()
    save_dir = tempfile.mkdtemp(prefix="microclaw_design27_")
    results: dict[str, bool] = {}
    try:
        results["B_3"] = b3_post_hardware_none(core, save_dir)
        results["B_4"] = b4_pre_hardware_none(core, save_dir)
        results["B_5"] = b5_image_process_none(core, save_dir)
        results["B_6"] = b6_raise_aborts_loudly(core, save_dir)
        results["B_7"] = b7_empty_list_probe(core, save_dir)   # last: may wedge
    except Exception:
        traceback.print_exc()
        results["B_gated"] = False
    finally:
        try:
            core.set_xy_position(x0, y0)
            core.wait_for_device(core.get_xy_stage_device())
        except Exception:
            pass
        shutil.rmtree(save_dir, ignore_errors=True)
    return results


def main() -> int:
    print(SEP)
    print("What does `return None` from a hook actually do?")
    print(SEP)
    try:
        import pycromanager  # noqa: F401
        import pyjavaz       # noqa: F401
    except ImportError:
        print("  pycro-manager / pyjavaz not importable — nothing to run.")
        return 1

    passed = {"B_1": b1_pyjavaz_sends_none_as_empty_object(),
              "B_2": b2_run_acq_hook_marshalling()}
    gated = run_gated_stages()
    if gated is not None:
        passed.update(gated)

    print(f"\n  SUMMARY  None on the wire: {'{} (no null exists)' if passed['B_1'] else 'DIFFERS'};"
          f"  hook thread: {'None and raise both deliver {}' if passed['B_2'] else 'DIFFERS'}")
    if gated is None:
        print("           engine half (B_3..B_7): SKIPPED — no MM on localhost:4827;"
              "\n           the ghost-exposure verdicts still need the demo-config machine"
              "\n           (the founding rig trace already shows B_3's core claim live).")
    else:
        print(f"           ghost exposures on skip: post_hw "
              f"{'CONFIRMED' if gated.get('B_3') else 'not shown'}, pre_hw "
              f"{'CONFIRMED' if gated.get('B_4') else 'not shown'};  processor None drops"
              f" nothing: {'CONFIRMED' if gated.get('B_5') else 'not shown'}")
        print(f"           raise is the working stop: "
              f"{'CONFIRMED' if gated.get('B_6') else 'NOT CONFIRMED'};  empty-list probe:"
              f" {'answered' if gated.get('B_7') else 'CONTRADICTS SOURCE'}")

    failed = [name for name, p in passed.items() if not p]
    if failed:
        print(f"\n  FAILED STAGES: {', '.join(failed)}")
    print("\n" + SEP)
    print("Findings are written up in design/27-return-none-skips-nothing.md.")
    rc = 1 if failed else 0
    if _B7_WEDGED:
        print("NOTE: B_7 wedged an acquisition thread; forcing process exit now.")
        sys.stdout.flush()
        os._exit(rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
