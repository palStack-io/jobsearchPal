"""Finish it here: the viewer's WebSocket carried to the browser box's VNC port, byte for byte (what websockify does,
so no other port is ever opened to the outside). Sign-in and site checks happen in the route, before this runs."""
from __future__ import annotations

import asyncio

from starlette.websockets import WebSocket, WebSocketDisconnect


def is_input(frame: bytes) -> bool:
    """True when a frame from the viewer holds a key, pointer or paste message (RFB client messages 4, 5, 6).

    The viewer also keeps asking for screen updates (message 3) while its tab is open; those are not you, so they must
    not count against the idle close. Handshake frames (version, security type, ClientInit) start with bytes that are
    not client message types, so they read as not input."""
    sizes = {0: 20, 3: 10, 4: 8, 5: 6}
    i = 0
    while i < len(frame):
        t = frame[i]
        if t in (4, 5, 6):
            return True
        if t in sizes:
            i += sizes[t]
        elif t == 2 and i + 4 <= len(frame):  # SetEncodings: 4 bytes + 4 per encoding
            i += 4 + 4 * int.from_bytes(frame[i + 2:i + 4], "big")
        else:
            return False
    return False


async def relay(ws: WebSocket, host: str, port: int, on_client_data) -> None:
    offered = ws.scope.get("subprotocols") or []
    await ws.accept(subprotocol="binary" if "binary" in offered else None)
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except OSError:
        await ws.close(code=1011)
        return

    async def up():
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                data = msg.get("bytes") or (msg.get("text") or "").encode()
                if data:
                    if is_input(data):
                        on_client_data()  # you typed, clicked or pasted: the session is in use
                    writer.write(data)
                    await writer.drain()
        except (WebSocketDisconnect, ConnectionError):
            pass

    async def down():
        try:
            while (data := await reader.read(65536)):
                await ws.send_bytes(data)
        except (WebSocketDisconnect, ConnectionError, RuntimeError):
            pass

    tasks = [asyncio.create_task(up()), asyncio.create_task(down())]
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in tasks:
        t.cancel()
    writer.close()
    try:
        await ws.close()
    except RuntimeError:
        pass  # already closed
