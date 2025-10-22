import asyncio
import json
from typing import Any, AsyncGenerator


async def use_stream_response(req_id: str) -> AsyncGenerator[Any, None]:
    from server import STREAM_QUEUE, logger
    import queue

    if STREAM_QUEUE is None:
        logger.warning(f"[{req_id}] STREAM_QUEUE is None, 无法使用流响应")
        return

    logger.info(f"[{req_id}] 开始使用流响应")

    empty_count = 0
    max_empty_retries = 300
    data_received = False

    try:
        while True:
            try:
                data = STREAM_QUEUE.get_nowait()
                if data is None:
                    logger.info(f"[{req_id}] 接收到流结束标志")
                    break
                empty_count = 0
                data_received = True
                logger.debug(f"[{req_id}] 接收到流数据: {type(data)} - {str(data)[:200]}...")

                if isinstance(data, str):
                    try:
                        parsed_data = json.loads(data)
                        if parsed_data.get("done") is True:
                            logger.info(f"[{req_id}] 接收到JSON格式的完成标志")
                            yield parsed_data
                            break
                        else:
                            yield parsed_data
                    except json.JSONDecodeError:
                        logger.debug(f"[{req_id}] 返回非JSON字符串数据")
                        yield data
                else:
                    yield data
                    if isinstance(data, dict) and data.get("done") is True:
                        logger.info(f"[{req_id}] 接收到字典格式的完成标志")
                        break
            except (queue.Empty, asyncio.QueueEmpty):
                empty_count += 1
                if empty_count % 50 == 0:
                    logger.info(f"[{req_id}] 等待流数据... ({empty_count}/{max_empty_retries})")
                if empty_count >= max_empty_retries:
                    if not data_received:
                        logger.error(f"[{req_id}] 流响应队列空读取次数达到上限且未收到任何数据，可能是辅助流未启动或出错")
                    else:
                        logger.warning(f"[{req_id}] 流响应队列空读取次数达到上限 ({max_empty_retries})，结束读取")
                    yield {"done": True, "reason": "internal_timeout", "body": "", "function": []}
                    return
                await asyncio.sleep(0.1)
                continue
    except Exception as e:
        logger.error(f"[{req_id}] 使用流响应时出错: {e}")
        raise
    finally:
        logger.info(f"[{req_id}] 流响应使用完成，数据接收状态: {data_received}")


async def clear_stream_queue():
    from server import STREAM_QUEUE, logger
    import queue

    if STREAM_QUEUE is None:
        logger.info("流队列未初始化或已被禁用，跳过清空操作。")
        return

    while True:
        try:
            data_chunk = await asyncio.to_thread(STREAM_QUEUE.get_nowait)
        except queue.Empty:
            logger.info("流式队列已清空 (捕获到 queue.Empty)。")
            break
        except Exception as e:
            logger.error(f"清空流式队列时发生意外错误: {e}", exc_info=True)
            break
    logger.info("流式队列缓存清空完毕。")

