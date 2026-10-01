from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/pos")
async def position_ws(websocket: WebSocket) -> None:
    """Fast player positions. Frames carry only the pids that moved; the first
    frame after connecting carries every known position."""
    await websocket.accept()
    services = websocket.app.state.services
    stream = services.get("position_stream")
    if stream is None:
        await websocket.close(code=1011, reason="position_stream not configured")
        return
    queue = stream.subscribe()
    try:
        wm = services.get("worker_manager")
        if wm is not None and hasattr(wm, "position_frame"):
            await websocket.send_json(wm.position_frame().model_dump())
        while True:
            frame = await queue.get()
            await websocket.send_json(frame.model_dump())
    except WebSocketDisconnect:
        pass
    finally:
        stream.unsubscribe(queue)
