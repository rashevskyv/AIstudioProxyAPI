"""
Queue worker module
Processes tasks in the request queue
"""

import asyncio
import time
from fastapi import HTTPException
from typing import Any, Dict, Optional, Tuple
from .error_utils import (
    client_disconnected,
    client_cancelled,
    processing_timeout,
    server_error,
)



async def queue_worker() -> None:
    """Queue worker that processes tasks in the request queue"""
    # Import global variables
    from server import (
        logger, request_queue, processing_lock, model_switching_lock, 
        params_cache_lock
    )
    
    logger.info("--- Queue Worker started ---")
    
    # Check and initialize globals
    if request_queue is None:
        logger.info("Initializing request_queue...")
        from asyncio import Queue
        request_queue = Queue()
    
    if processing_lock is None:
        logger.info("Initializing processing_lock...")
        from asyncio import Lock
        processing_lock = Lock()
    
    if model_switching_lock is None:
        logger.info("Initializing model_switching_lock...")
        from asyncio import Lock
        model_switching_lock = Lock()
    
    if params_cache_lock is None:
        logger.info("Initializing params_cache_lock...")
        from asyncio import Lock
        params_cache_lock = Lock()
    
    was_last_request_streaming = False
    last_request_completion_time = 0
    
    while True:
        request_item = None
        result_future = None
        req_id = "UNKNOWN"
        completion_event = None
        
        try:
            # Check items in queue, mark disconnected client requests as cancelled
            queue_size = request_queue.qsize()
            if queue_size > 0:
                checked_count = 0
                items_to_requeue = []
                processed_ids = set()
                
                while checked_count < queue_size and checked_count < 10:
                    try:
                        item = request_queue.get_nowait()
                        item_req_id = item.get("req_id", "unknown")
                        
                        if item_req_id in processed_ids:
                            items_to_requeue.append(item)
                            continue
                            
                        processed_ids.add(item_req_id)
                        
                        if not item.get("cancelled", False):
                            item_http_request = item.get("http_request")
                            if item_http_request:
                                try:
                                    if await item_http_request.is_disconnected():
                                        logger.info(f"[{item_req_id}] (Worker Queue Check) Detected client disconnected; marking as cancelled.")
                                        item["cancelled"] = True
                                        item_future = item.get("result_future")
                                        if item_future and not item_future.done():
                                            item_future.set_exception(client_disconnected(item_req_id, "Client disconnected while queued."))
                                except Exception as check_err:
                                    logger.error(f"[{item_req_id}] (Worker Queue Check) Error checking disconnect: {check_err}")
                        
                        items_to_requeue.append(item)
                        checked_count += 1
                    except asyncio.QueueEmpty:
                        break
                
                for item in items_to_requeue:
                    await request_queue.put(item)
            
            # Get next request
            try:
                request_item = await asyncio.wait_for(request_queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                # No new requests within 5s; continue loop
                continue
            
            req_id = request_item["req_id"]
            request_data = request_item["request_data"]
            http_request = request_item["http_request"]
            result_future = request_item["result_future"]

            if request_item.get("cancelled", False):
                logger.info(f"[{req_id}] (Worker) Request was cancelled; skipping.")
                if not result_future.done():
                    result_future.set_exception(client_cancelled(req_id, "Request was cancelled by user"))
                request_queue.task_done()
                continue

            is_streaming_request = request_data.stream
            logger.info(f"[{req_id}] (Worker) Took request. Mode: {'stream' if is_streaming_request else 'non-stream'}")

            # Optimization: proactively check client connection before processing to avoid unnecessary work
            from api_utils.request_processor import _test_client_connection
            is_connected = await _test_client_connection(req_id, http_request)
            if not is_connected:
                logger.info(f"[{req_id}] (Worker) ✅ Proactively detected client disconnected; skipping to save resources")
                if not result_future.done():
                    result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] Client disconnected before processing started"))
                request_queue.task_done()
                continue
            
            # Streaming requests pacing
            current_time = time.time()
            if was_last_request_streaming and is_streaming_request and (current_time - last_request_completion_time < 1.0):
                delay_time = max(0.5, 1.0 - (current_time - last_request_completion_time))
                logger.info(f"[{req_id}] (Worker) Consecutive streaming request; adding {delay_time:.2f}s delay...")
                await asyncio.sleep(delay_time)
            
            # Before waiting for lock, check client connection again
            is_connected = await _test_client_connection(req_id, http_request)
            if not is_connected:
                logger.info(f"[{req_id}] (Worker) ✅ Detected client disconnect while waiting for lock; cancelling processing")
                if not result_future.done():
                    result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] Client closed the request"))
                request_queue.task_done()
                continue
            
            logger.info(f"[{req_id}] (Worker) Waiting for processing lock...")
            async with processing_lock:
                logger.info(f"[{req_id}] (Worker) Acquired processing lock. Starting core processing...")
                
                # Final proactive check after acquiring lock
                is_connected = await _test_client_connection(req_id, http_request)
                if not is_connected:
                    logger.info(f"[{req_id}] (Worker) ✅ Detected client disconnect after acquiring lock; cancelling processing")
                    if not result_future.done():
                        result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] Client closed the request"))
                elif result_future.done():
                    logger.info(f"[{req_id}] (Worker) Future completed/cancelled before processing. Skipping.")
                else:
                    # Call actual request processing function
                    try:
                        from api_utils import _process_request_refactored
                        returned_value = await _process_request_refactored(
                            req_id, request_data, http_request, result_future
                        )
                        
                        completion_event, submit_btn_loc, client_disco_checker = None, None, None
                        current_request_was_streaming = False

                        if isinstance(returned_value, tuple) and len(returned_value) == 3:
                            completion_event, submit_btn_loc, client_disco_checker = returned_value
                            if completion_event is not None:
                                current_request_was_streaming = True
                                logger.info(f"[{req_id}] (Worker) _process_request_refactored returned stream info (event, locator, checker).")
                            else:
                                current_request_was_streaming = False
                                logger.info(f"[{req_id}] (Worker) _process_request_refactored returned a tuple, but completion_event is None (likely non-stream or early exit).")
                        elif returned_value is None:
                            current_request_was_streaming = False
                            logger.info(f"[{req_id}] (Worker) _process_request_refactored returned non-stream completion (None).")
                        else:
                            current_request_was_streaming = False
                            logger.warning(f"[{req_id}] (Worker) _process_request_refactored returned unexpected type: {type(returned_value)}")

                        # Unified client disconnect monitoring and response handling
                        if completion_event:
                            # Streaming: wait for completion_event
                            logger.info(f"[{req_id}] (Worker) Waiting for stream generator completion signal...")

                            # Enhanced disconnect monitor to trigger early done signal
                            client_disconnected_early = False

                            async def enhanced_disconnect_monitor():
                                nonlocal client_disconnected_early
                                while not completion_event.is_set():
                                    try:
                                        is_connected = await _test_client_connection(req_id, http_request)
                                        if not is_connected:
                                            logger.info(f"[{req_id}] (Worker) ✅ Detected client disconnect during streaming; triggering early done")
                                            client_disconnected_early = True
                                            if not completion_event.is_set():
                                                completion_event.set()
                                            break
                                        await asyncio.sleep(0.3)
                                    except Exception as e:
                                        logger.error(f"[{req_id}] (Worker) Enhanced disconnect monitor error: {e}")
                                        break

                            disconnect_monitor_task = asyncio.create_task(enhanced_disconnect_monitor())
                        else:
                            # Non-stream: wait for result and monitor disconnect
                            logger.info(f"[{req_id}] (Worker) Non-stream mode; waiting for processing completion...")

                            client_disconnected_early = False

                            async def non_streaming_disconnect_monitor():
                                nonlocal client_disconnected_early
                                while not result_future.done():
                                    try:
                                        is_connected = await _test_client_connection(req_id, http_request)
                                        if not is_connected:
                                            logger.info(f"[{req_id}] (Worker) ✅ Detected client disconnect during non-stream; cancelling processing")
                                            client_disconnected_early = True
                                            if not result_future.done():
                                                result_future.set_exception(HTTPException(status_code=499, detail=f"[{req_id}] Client disconnected during non-stream processing"))
                                            break
                                        await asyncio.sleep(0.3)
                                    except Exception as e:
                                        logger.error(f"[{req_id}] (Worker) Non-stream disconnect monitor error: {e}")
                                        break

                            disconnect_monitor_task = asyncio.create_task(non_streaming_disconnect_monitor())

                        # Wait for completion (stream or non-stream)
                        try:
                            if completion_event:
                                from server import RESPONSE_COMPLETION_TIMEOUT
                                await asyncio.wait_for(completion_event.wait(), timeout=RESPONSE_COMPLETION_TIMEOUT/1000 + 60)
                                logger.info(f"[{req_id}] (Worker) ✅ Stream generator completion signal received. Client disconnected early: {client_disconnected_early}")
                            else:
                                from server import RESPONSE_COMPLETION_TIMEOUT
                                await asyncio.wait_for(asyncio.shield(result_future), timeout=RESPONSE_COMPLETION_TIMEOUT/1000 + 60)
                                logger.info(f"[{req_id}] (Worker) ✅ Non-stream processing completed. Client disconnected early: {client_disconnected_early}")

                            if client_disconnected_early:
                                logger.info(f"[{req_id}] (Worker) Client disconnected early; skipping button state handling")
                            elif submit_btn_loc and client_disco_checker and completion_event:
                                    logger.info(f"[{req_id}] (Worker) Stream completed; checking and handling submit button state...")
                                    wait_timeout_ms = 30000
                                    try:
                                        from playwright.async_api import expect as expect_async
                                        from api_utils.request_processor import ClientDisconnectedError

                                        client_disco_checker("Post-stream button state pre-check: ")
                                        await asyncio.sleep(0.5)

                                        logger.info(f"[{req_id}] (Worker) Checking submit button state...")
                                        try:
                                            is_button_enabled = await submit_btn_loc.is_enabled(timeout=2000)
                                            logger.info(f"[{req_id}] (Worker) Submit button enabled state: {is_button_enabled}")

                                            if is_button_enabled:
                                                logger.info(f"[{req_id}] (Worker) Stream finished but button still enabled; clicking to stop generation...")
                                                await submit_btn_loc.click(timeout=5000, force=True)
                                                logger.info(f"[{req_id}] (Worker) ✅ Submit button click done.")
                                            else:
                                                logger.info(f"[{req_id}] (Worker) Submit button is disabled; no click needed.")
                                        except Exception as button_check_err:
                                            logger.warning(f"[{req_id}] (Worker) Failed checking button state: {button_check_err}")

                                        logger.info(f"[{req_id}] (Worker) Waiting for submit button to become disabled...")
                                        await expect_async(submit_btn_loc).to_be_disabled(timeout=wait_timeout_ms)
                                        logger.info(f"[{req_id}] ✅ Submit button is disabled.")

                                    except Exception as e_pw_disabled:
                                        logger.warning(f"[{req_id}] ⚠️ Post-stream submit button handling timeout/error: {e_pw_disabled}")
                                        from api_utils.request_processor import save_error_snapshot
                                        await save_error_snapshot(f"stream_post_submit_button_handling_timeout_{req_id}")
                                    except ClientDisconnectedError:
                                        logger.info(f"[{req_id}] Client disconnected during post-stream button handling.")
                            elif completion_event and current_request_was_streaming:
                                logger.warning(f"[{req_id}] (Worker) Streaming request but submit_btn_loc or client_disco_checker not provided. Skipping button disabled wait.")

                        except asyncio.TimeoutError:
                            logger.warning(f"[{req_id}] (Worker) ⚠️ Timeout while waiting for processing completion.")
                            if not result_future.done():
                                result_future.set_exception(processing_timeout(req_id, "Processing timed out waiting for completion."))
                        except Exception as ev_wait_err:
                            logger.error(f"[{req_id}] (Worker) ❌ Error while waiting for processing completion: {ev_wait_err}")
                            if not result_future.done():
                                result_future.set_exception(server_error(req_id, f"Error waiting for completion: {ev_wait_err}"))
                        finally:
                            if 'disconnect_monitor_task' in locals() and not disconnect_monitor_task.done():
                                disconnect_monitor_task.cancel()
                                try:
                                    await disconnect_monitor_task
                                except asyncio.CancelledError:
                                    pass

                    except Exception as process_err:
                        logger.error(f"[{req_id}] (Worker) _process_request_refactored execution error: {process_err}")
                        if not result_future.done():
                            result_future.set_exception(server_error(req_id, f"Request processing error: {process_err}"))
            
            logger.info(f"[{req_id}] (Worker) Releasing processing lock.")

            # Immediately perform cleanup after releasing the lock
            try:
                from api_utils import clear_stream_queue
                await clear_stream_queue()

                if submit_btn_loc and client_disco_checker:
                    from server import page_instance, is_page_ready
                    from config.constants import ENABLE_CONTINUOUS_CHAT
                    if not ENABLE_CONTINUOUS_CHAT:
                        if page_instance and is_page_ready:
                            from browser_utils.page_controller import PageController
                            page_controller = PageController(page_instance, logger, req_id)
                            logger.info(f"[{req_id}] (Worker) Clearing chat history ({'stream' if completion_event else 'non-stream'} mode)...")
                            await page_controller.clear_chat_history(client_disco_checker)
                            logger.info(f"[{req_id}] (Worker) ✅ Chat history cleared.")
                    else:
                        logger.info(f"[{req_id}] (Worker) Continuous chat mode enabled; skipping chat history clearing.")
                else:
                    logger.info(f"[{req_id}] (Worker) Skipping chat history clearing: missing parameters (submit_btn_loc: {bool(submit_btn_loc)}, client_disco_checker: {bool(client_disco_checker)})")
            except Exception as clear_err:
                logger.error(f"[{req_id}] (Worker) Error during cleanup operations: {clear_err}", exc_info=True)

            was_last_request_streaming = is_streaming_request
            last_request_completion_time = time.time()
            
        except asyncio.CancelledError:
            logger.info("--- Queue Worker cancelled ---")
            if result_future and not result_future.done():
                result_future.cancel("Worker cancelled")
            break
        except Exception as e:
            logger.error(f"[{req_id}] (Worker) ❌ Unexpected error while processing request: {e}", exc_info=True)
            if result_future and not result_future.done():
                result_future.set_exception(server_error(req_id, f"Internal server error: {e}"))
        finally:
            if request_item:
                request_queue.task_done()
    
    logger.info("--- Queue Worker stopped ---") 
