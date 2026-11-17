"""Finish it here: the viewer's WebSocket carried to the browser box's VNC port, byte for byte (what websockify does,
so no other port is ever opened to the outside). Sign-in and site checks happen in the route, before this runs."""
from __future__ import annotations

import asyncio

from starlette.websockets import WebSocket, WebSocketDisconnect


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
                    on_client_data()
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
