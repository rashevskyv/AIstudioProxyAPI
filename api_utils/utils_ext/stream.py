import asyncio
import json
from typing import Any, AsyncGenerator


async def use_stream_response(req_id: str) -> AsyncGenerator[Any, None]:
    from server import STREAM_QUEUE, logger
    import queue

    if STREAM_QUEUE is None:
        logger.warning(f"[{req_id}] STREAM_QUEUE is None; cannot use stream response")
        return

    logger.info(f"[{req_id}] Start using stream response")

    empty_count = 0
    max_empty_retries = 300
    data_received = False

    try:
        while True:
            try:
                data = STREAM_QUEUE.get_nowait()
                if data is None:
                    logger.info(f"[{req_id}] Received stream end marker")
                    break
                empty_count = 0
                data_received = True
                logger.debug(f"[{req_id}] Received stream data: {type(data)} - {str(data)[:200]}...")

                if isinstance(data, str):
                    try:
                        parsed_data = json.loads(data)
                        if parsed_data.get("done") is True:
                            logger.info(f"[{req_id}] Received completion flag (JSON)")
                            yield parsed_data
                            break
                        else:
                            yield parsed_data
                    except json.JSONDecodeError:
                        logger.debug(f"[{req_id}] Returning non-JSON string data")
                        yield data
                else:
                    yield data
                    if isinstance(data, dict) and data.get("done") is True:
                        logger.info(f"[{req_id}] Received completion flag (dict)")
                        break
            except (queue.Empty, asyncio.QueueEmpty):
                empty_count += 1
                if empty_count % 50 == 0:
                    logger.info(f"[{req_id}] Waiting for stream data... ({empty_count}/{max_empty_retries})")
                if empty_count >= max_empty_retries:
                    if not data_received:
                        logger.error(f"[{req_id}] Stream queue empty limit reached without any data; auxiliary stream may not have started or crashed")
                    else:
                        logger.warning(f"[{req_id}] Stream queue empty read limit reached ({max_empty_retries}); ending read")
                    yield {"done": True, "reason": "internal_timeout", "body": "", "function": []}
                    return
                await asyncio.sleep(0.1)
                continue
    except Exception as e:
        logger.error(f"[{req_id}] Error while using stream response: {e}")
        raise
    finally:
        logger.info(f"[{req_id}] Stream response finished. Data received: {data_received}")


async def clear_stream_queue():
    from server import STREAM_QUEUE, logger
    import queue

    if STREAM_QUEUE is None:
        logger.info("Stream queue not initialized or disabled; skip clearing.")
        return

    while True:
        try:
            data_chunk = await asyncio.to_thread(STREAM_QUEUE.get_nowait)
        except queue.Empty:
            logger.info("Stream queue cleared (caught queue.Empty).")
            break
        except Exception as e:
            logger.error(f"Unexpected error while clearing stream queue: {e}", exc_info=True)
            break
    logger.info("Stream queue buffer clearing completed.")
