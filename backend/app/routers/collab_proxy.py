"""Same-origin WebSocket proxy: browser  ->  web /collab/<room>  ->  collab service.

With this, the whole application is reachable through one port (direct,
behind nginx, or over Tailscale) without exposing the collab service. The
collab service still authenticates every connection with the signed token in
the query string; this proxy only forwards frames.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from ..config import get_settings

router = APIRouter()
log = logging.getLogger("prism.collab_proxy")


@router.websocket("/collab/{room}")
async def collab_proxy(ws: WebSocket, room: str):
    s = get_settings()
    if not s.collab_enabled or not room.isalnum():
        await ws.close(code=4404)
        return
    base = s.collab_internal_url.rstrip("/")
    upstream_url = ("ws" + base[4:] if base.startswith("http") else base) + f"/collab/{room}"
    if ws.url.query:
        upstream_url += "?" + ws.url.query
    try:
        upstream = await connect(upstream_url, max_size=16 * 1024 * 1024, open_timeout=10, compression=None)
    except InvalidStatus as e:
        # 401 from the collab service: invalid/expired token or wrong project
        await ws.close(code=4401 if e.response.status_code == 401 else 1011)
        return
    except (OSError, asyncio.TimeoutError) as e:
        log.warning("collab service unreachable: %s", e)
        await ws.close(code=1013)
        return
    await ws.accept()

    async def client_to_upstream():
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    await upstream.send(msg["bytes"])
                elif msg.get("text") is not None:
                    await upstream.send(msg["text"])
        except (WebSocketDisconnect, ConnectionClosed):
            pass

    async def upstream_to_client():
        try:
            async for data in upstream:
                if isinstance(data, bytes):
                    await ws.send_bytes(data)
                else:
                    await ws.send_text(data)
        except (WebSocketDisconnect, ConnectionClosed, RuntimeError):
            pass

    tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await upstream.close()
        try:
            await ws.close()
        except RuntimeError:
            pass
