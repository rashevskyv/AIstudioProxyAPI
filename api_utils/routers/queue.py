import time
import logging
from asyncio import Queue, Lock
from fastapi import Depends
from fastapi.responses import JSONResponse
from ..dependencies import get_logger, get_request_queue, get_processing_lock
from fastapi import HTTPException
from ..error_utils import client_cancelled


async def cancel_queued_request(req_id: str, request_queue: Queue, logger: logging.Logger) -> bool:
    items_to_requeue = []
    found = False
    try:
        while not request_queue.empty():
            item = request_queue.get_nowait()
            if item.get("req_id") == req_id:
                logger.info(f"[{req_id}] 在队列中找到请求，标记为已取消。")
                item["cancelled"] = True
                if (future := item.get("result_future")) and not future.done():
                    future.set_exception(client_cancelled(req_id))
                found = True
            items_to_requeue.append(item)
    finally:
        for item in items_to_requeue:
            await request_queue.put(item)
    return found


async def cancel_request(
    req_id: str,
    logger: logging.Logger = Depends(get_logger),
    request_queue: Queue = Depends(get_request_queue)
):
    logger.info(f"[{req_id}] 收到取消请求。")
    if await cancel_queued_request(req_id, request_queue, logger):
        return JSONResponse(content={"success": True, "message": f"Request {req_id} marked as cancelled."})
    else:
        return JSONResponse(status_code=404, content={"success": False, "message": f"Request {req_id} not found in queue."})


async def get_queue_status(
    request_queue: Queue = Depends(get_request_queue),
    processing_lock: Lock = Depends(get_processing_lock)
):
    try:
        queue_items = list(request_queue._queue)
    except Exception:
        queue_items = []
    return JSONResponse(content={
        "queue_length": len(queue_items),
        "is_processing_locked": processing_lock.locked(),
        "items": sorted([
            {
                "req_id": item.get("req_id", "unknown"),
                "enqueue_time": item.get("enqueue_time", 0),
                "wait_time_seconds": round(time.time() - item.get("enqueue_time", 0), 2),
                "is_streaming": item.get("request_data").stream,
                "cancelled": item.get("cancelled", False)
            } for item in queue_items
        ], key=lambda x: x.get("enqueue_time", 0))
    })
