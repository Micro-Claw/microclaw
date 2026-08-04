import socket
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
        stop.set()
        listener.close()
        thread.join()

    assert time.monotonic() - started < 4
    assert ready is False
    assert "no working Micro-Manager ZMQ bridge" in message or "within 2 seconds" in message
