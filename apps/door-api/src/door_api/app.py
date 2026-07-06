"""ASGI application for door-api.

Exposes the WebSocket broadcast and health/metrics endpoints.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from door_api.broadcast import DisplayBroadcast
from door_api.config import SessionConfig
from door_api.persistence import SessionStore
from door_api.session import SessionMachine


class DoorApiState:
    """State container for the FastAPI app."""

    def __init__(self) -> None:
        self.broadcast = DisplayBroadcast()
        self.config = SessionConfig.from_env()
        self.store = SessionStore(self.config.db_path)

        def on_event(event: dict[str, Any]) -> None:
            # Send delta to all connected clients.
            self.broadcast.send_delta(event)
            # Update the snapshot when the session state changes.
            if event["type"] in ("session.state_changed", "session.started", "session.ended"):
                self.broadcast.update_snapshot(self.machine.snapshot().to_dict())

        self.machine = SessionMachine(config=self.config, store=self.store, on_event=on_event)

    def startup(self) -> None:
        """Start the machine and populate the initial snapshot."""
        self.machine.restore_from_persistence()
        self.broadcast.update_snapshot(self.machine.snapshot().to_dict())

    def shutdown(self) -> None:
        """Close resources."""
        self.machine.close()


state = DoorApiState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.startup()
    yield
    state.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> JSONResponse:
    return JSONResponse(content=state.machine.metrics.to_dict())


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = state.broadcast.make_client_queue()
    try:
        while True:
            # Send messages from the client's queue.
            msg = await queue.get()
            await websocket.send_text(msg)
            queue.task_done()
    except WebSocketDisconnect:
        pass
    finally:
        state.broadcast.remove_client(queue)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("door_api.app:app", host="0.0.0.0", port=8000, reload=True)
