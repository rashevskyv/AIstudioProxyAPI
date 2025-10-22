import uuid
import logging
from fastapi import Depends, WebSocket, WebSocketDisconnect
from ..dependencies import get_logger, get_log_ws_manager
from models import WebSocketConnectionManager


async def websocket_log_endpoint(
    websocket: WebSocket,
    logger: logging.Logger = Depends(get_logger),
    log_ws_manager: WebSocketConnectionManager = Depends(get_log_ws_manager)
):
    if not log_ws_manager:
        await websocket.close(code=1011)
        return

    client_id = str(uuid.uuid4())
    try:
        await log_ws_manager.connect(client_id, websocket)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"日志 WebSocket (客户端 {client_id}) 发生异常: {e}", exc_info=True)
    finally:
        log_ws_manager.disconnect(client_id)
