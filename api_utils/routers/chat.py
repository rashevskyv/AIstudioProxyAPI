import os
import random
import time
import logging
from asyncio import Queue, Future
from fastapi import Depends, HTTPException, Request
from ..dependencies import get_logger, get_request_queue, get_server_state, get_worker_task, get_page_instance
from config import RESPONSE_COMPLETION_TIMEOUT
from models import ChatCompletionRequest
import asyncio
from fastapi.responses import JSONResponse
from config import get_environment_variable
from ..error_utils import service_unavailable
from browser_utils.operations import create_new_chat, click_run_button, click_stop_button


async def chat_completions(
    request: ChatCompletionRequest,
    http_request: Request,
    logger: logging.Logger = Depends(get_logger),
    request_queue: Queue = Depends(get_request_queue),
    server_state: dict = Depends(get_server_state),
    worker_task = Depends(get_worker_task)
) -> JSONResponse:
    req_id = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=7))
    logger.info(f"[{req_id}] Received /v1/chat/completions request (Stream={request.stream})")

    launch_mode = get_environment_variable('LAUNCH_MODE', 'unknown')
    browser_page_critical = launch_mode != "direct_debug_no_browser"

    service_unavailable_flag = server_state["is_initializing"] or \
                          not server_state["is_playwright_ready"] or \
                          (browser_page_critical and (not server_state["is_page_ready"] or not server_state["is_browser_connected"])) or \
                          not worker_task or worker_task.done()

    if service_unavailable_flag:
        raise service_unavailable(req_id)

    result_future = Future()
    await request_queue.put({
        "req_id": req_id, "request_data": request, "http_request": http_request,
        "result_future": result_future, "enqueue_time": time.time(), "cancelled": False
    })

    try:
        timeout_seconds = RESPONSE_COMPLETION_TIMEOUT / 1000 + 120
        return await asyncio.wait_for(result_future, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"[{req_id}] Request processing timed out.")
    except asyncio.CancelledError:
        raise HTTPException(status_code=499, detail=f"[{req_id}] Request was cancelled by the client.")
    except HTTPException as http_exc:
        if http_exc.status_code == 499:
            logger.info(f"[{req_id}] Client disconnected: {http_exc.detail}")
        else:
            logger.warning(f"[{req_id}] HTTP exception: {http_exc.detail}")
        raise http_exc
    except Exception as e:
        logger.exception(f"[{req_id}] Error while awaiting Worker response")
        raise HTTPException(status_code=500, detail=f"[{req_id}] Internal server error: {e}")


async def new_chat_endpoint(
    page_instance = Depends(get_page_instance),
    logger: logging.Logger = Depends(get_logger)
) -> JSONResponse:
    req_id = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=7))
    logger.info(f"[{req_id}] ACTION: /api/new-chat requested (create new chat)")
    if not page_instance or page_instance.is_closed():
        logger.error(f"[{req_id}] ACTION-FAIL: new chat - browser page not available")
        raise HTTPException(status_code=503, detail="Browser page is not available.")

    success = await create_new_chat(page_instance, req_id)
    if success:
        logger.info(f"[{req_id}] ACTION-SUCCESS: new chat created")
        return JSONResponse(content={"success": True, "message": "New chat created successfully."})
    else:
        raise HTTPException(status_code=500, detail="Failed to create a new chat.")

async def click_run_endpoint(
    http_request: Request,
    page_instance = Depends(get_page_instance),
    logger: logging.Logger = Depends(get_logger)
) -> JSONResponse:
    req_id = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=7))
    logger.info(f"[{req_id}] ACTION: /api/click-run requested (click Run)")
    if not page_instance or page_instance.is_closed():
        raise HTTPException(status_code=503, detail="Browser page is not available.")
    try:
        body = await http_request.json()
    except Exception:
        body = {}
    delay_ms = int(body.get('delay_ms', 0) or 0)
    ok = await click_run_button(page_instance, req_id, delay_ms=delay_ms)
    if ok:
        logger.info(f"[{req_id}] ACTION-SUCCESS: Run clicked (delay_ms={delay_ms})")
        return JSONResponse(content={"success": True, "message": "Run clicked.", "delay_ms": delay_ms})
    raise HTTPException(status_code=500, detail="Failed to click Run.")

async def click_stop_endpoint(
    http_request: Request,
    page_instance = Depends(get_page_instance),
    logger: logging.Logger = Depends(get_logger)
) -> JSONResponse:
    req_id = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=7))
    logger.info(f"[{req_id}] ACTION: /api/click-stop requested (click Stop)")
    if not page_instance or page_instance.is_closed():
        raise HTTPException(status_code=503, detail="Browser page is not available.")
    try:
        body = await http_request.json()
    except Exception:
        body = {}
    delay_ms = int(body.get('delay_ms', 0) or 0)
    ok = await click_stop_button(page_instance, req_id, delay_ms=delay_ms)
    if ok:
        logger.info(f"[{req_id}] ACTION-SUCCESS: Stop clicked (delay_ms={delay_ms})")
        return JSONResponse(content={"success": True, "message": "Stop clicked.", "delay_ms": delay_ms})
    raise HTTPException(status_code=500, detail="Failed to click Stop.")


async def scroll_to_top_endpoint(
    page_instance = Depends(get_page_instance),
    logger: logging.Logger = Depends(get_logger)
) -> JSONResponse:
    req_id = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=7))
    logger.info(f"[{req_id}] ACTION: /api/scroll-to-top requested (scroll to top)")
    if not page_instance or page_instance.is_closed():
        raise HTTPException(status_code=503, detail="Browser page is not available.")
    
    # Import PageController here to avoid circular imports
    from browser_utils.page_controller import PageController
    
    # Create a temporary PageController instance to use its methods
    page_controller = PageController(page_instance, logger, req_id)
    
    # Use a dummy check_client_disconnected function
    async def check_client_disconnected(stage: str) -> bool:
        return False
    
    await page_controller.scroll_to_top(check_client_disconnected)
    logger.info(f"[{req_id}] ACTION-SUCCESS: Scrolled to top of page")
    return JSONResponse(content={"success": True, "message": "Scrolled to top of page."})


async def scroll_to_bottom_endpoint(
    page_instance = Depends(get_page_instance),
    logger: logging.Logger = Depends(get_logger)
) -> JSONResponse:
    req_id = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=7))
    logger.info(f"[{req_id}] ACTION: /api/scroll-to-bottom requested (scroll to bottom)")
    if not page_instance or page_instance.is_closed():
        raise HTTPException(status_code=503, detail="Browser page is not available.")
    
    # Import PageController here to avoid circular imports
    from browser_utils.page_controller import PageController
    
    # Create a temporary PageController instance to use its methods
    page_controller = PageController(page_instance, logger, req_id)
    
    # Use a dummy check_client_disconnected function
    async def check_client_disconnected(stage: str) -> bool:
        return False
    
    await page_controller.scroll_to_bottom(check_client_disconnected)
    logger.info(f"[{req_id}] ACTION-SUCCESS: Scrolled to bottom of page")
    return JSONResponse(content={"success": True, "message": "Scrolled to bottom of page."})
