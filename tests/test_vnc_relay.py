"""The viewer's WebSocket: relayed to the browser box's VNC port, and only for a signed-in person on this site.
An echo server stands in for VNC. (The HTTP sign-in middleware does not run for WebSockets: the route checks itself.)"""
import socket
import threading

import pytest
from starlette.websockets import WebSocketDisconnect

from test_finish_here_web import FakeSessions, _setup


@pytest.fixture
def echo():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    stop = threading.Event()

    def serve():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            def pump(c=conn):
                with c:
                    while (data := c.recv(4096)):
                        c.sendall(data)
            threading.Thread(target=pump, daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    yield srv.getsockname()[1]
    stop.set()
    srv.close()


def _client(tmp_path, port, signed_in=True, state="ready"):
    db, c, fake = _setup(tmp_path, {"on_server": True, "host": "127.0.0.1", "cdp_port": 9, "vnc_port": port},
                         signed_in=signed_in)
    fake.st["state"] = state
    return c, fake


ORIGIN = {"origin": "http://testserver"}


def test_relay_echoes_bytes(tmp_path, echo):
    c, _ = _client(tmp_path, echo)
    with c.websocket_connect("/finish-here/vnc", headers=ORIGIN, subprotocols=["binary"]) as ws:
        ws.send_bytes(b"RFB hello")
        assert ws.receive_bytes() == b"RFB hello"


def _close_code(c, headers):
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/finish-here/vnc", headers=headers) as ws:
            ws.receive_bytes()
    return e.value.code


def test_no_cookie_closes_4401(tmp_path, echo):
    c, _ = _client(tmp_path, echo)
    c.cookies.clear()
    assert _close_code(c, ORIGIN) == 4401


def test_foreign_origin_closes_4403(tmp_path, echo):
    c, _ = _client(tmp_path, echo)
    assert _close_code(c, {"origin": "https://evil.example"}) == 4403
    assert _close_code(c, {}) == 4403  # no Origin at all: not a browser on this site


def test_no_session_closes_4404(tmp_path, echo):
    c, _ = _client(tmp_path, echo, state="closed")
    assert _close_code(c, ORIGIN) == 4404


def test_off_closes_4404(tmp_path, echo):
    _, c, _ = _setup(tmp_path)  # Finish here not turned on
    assert _close_code(c, ORIGIN) == 4404


KEY = bytes([4, 1, 0, 0, 0, 0, 0, 0x61])                 # KeyEvent: "a" down
POINTER = bytes([5, 1, 0, 10, 0, 20])                     # PointerEvent: button 1 at 10,20
UPDATE = bytes([3, 1, 0, 0, 0, 0, 5, 0, 3, 132])          # FramebufferUpdateRequest: the viewer asks for pixels


def _touches(tmp_path, echo, frames):
    c, fake = _client(tmp_path, echo)
    touched = []
    fake.touch = lambda: touched.append(1)
    with c.websocket_connect("/finish-here/vnc", headers=ORIGIN) as ws:
        for f in frames:
            ws.send_bytes(f)
            ws.receive_bytes()
    return len(touched)


def test_input_touches_session(tmp_path, echo):
    assert _touches(tmp_path, echo, [KEY]) == 1
    assert _touches(tmp_path, echo, [POINTER]) == 1
    assert _touches(tmp_path, echo, [UPDATE + KEY]) == 1     # an input message after a request in one frame


def test_an_open_viewer_alone_is_not_input(tmp_path, echo):
    """noVNC keeps asking for screen updates while the tab is open; that must not hold the session open forever.
    Found end to end (2026-11-17): the 20-minute idle close never fired with a viewer tab left open."""
    handshake = [b"RFB 003.008\n", b"\x01", b"\x01"]       # version, security type, ClientInit (shared)
    assert _touches(tmp_path, echo, [*handshake, UPDATE, UPDATE]) == 0
