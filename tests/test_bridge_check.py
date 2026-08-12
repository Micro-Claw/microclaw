import socket
import subprocess
import threading
import time

from microclaw.bridge_check import probe_bridge


def test_tcp_listener_without_zmq_handshake_is_not_ready():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    stop = threading.Event()

    def accept_without_speaking_zmq():
        listener.settimeout(0.1)
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            with connection:
                stop.wait(2)

    thread = threading.Thread(target=accept_without_speaking_zmq)
    thread.start()
    started = time.monotonic()
    try:
        ready, message = probe_bridge(port, 2)
    finally:
        # Join BEFORE closing. The helper polls accept() on a 0.1 s timeout and
        # exits within one poll of stop.set(), but closing the socket out from
        # under a thread still parked in accept() raises OSError there -- on
        # Windows, WinError 10038 -- which pytest surfaces as an unhandled thread
        # exception warning. The test still passed, so the only symptom was a
        # fourth warning appearing intermittently in rig-gate runs that state an
        # expected warning count.
        stop.set()
        thread.join()
        listener.close()

    assert time.monotonic() - started < 4
    assert ready is False
    assert message == (
        f"No working Micro-Manager ZMQ bridge answered on port {port} within 2 seconds."
    )


def test_worker_traceback_is_not_shown_to_operator(monkeypatch):
    traceback = "Exception in thread BridgeSocketThread_port4827:\nTraceback...\nuseful last line"
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", traceback),
    )

    ready, message = probe_bridge(4827, 5)

    assert ready is False
    assert message == (
        "No working Micro-Manager ZMQ bridge answered on port 4827 within 5 seconds."
    )
    assert "Traceback" not in message
