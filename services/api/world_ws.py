from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/world")
async def world_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    stream = websocket.app.state.services.get("world_stream")
    if stream is None:
        await websocket.close(code=1011, reason="world_stream not configured")
        return
    queue = stream.subscribe()
    try:
        while True:
            snap = await queue.get()
            await websocket.send_json(snap.model_dump())
    except WebSocketDisconnect:
        pass
    finally:
        stream.unsubscribe(queue)
