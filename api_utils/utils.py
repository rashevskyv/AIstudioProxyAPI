"""
API工具函数模块
包含SSE生成、流处理、token统计和请求验证等工具函数
"""

import asyncio
import json
import time
import datetime
from typing import Any, Dict, List, Optional, AsyncGenerator, Tuple, Union
from asyncio import Queue
from models import Message
import re
import base64
import requests
import os
import hashlib
from urllib.parse import urlparse, unquote
from .tools_registry import execute_tool_call


# --- SSE生成函数 ---
def generate_sse_chunk(delta: str, req_id: str, model: str) -> str:
    """生成SSE数据块"""
    chunk_data = {
        "id": f"chatcmpl-{req_id}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}]
    }
    return f"data: {json.dumps(chunk_data)}\n\n"


def generate_sse_stop_chunk(req_id: str, model: str, reason: str = "stop", usage: dict = None) -> str:
    """生成SSE停止块"""
    stop_chunk_data = {
        "id": f"chatcmpl-{req_id}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]
    }
    
    # 添加usage信息（如果提供）
    if usage:
        stop_chunk_data["usage"] = usage
    
    return f"data: {json.dumps(stop_chunk_data)}\n\ndata: [DONE]\n\n"


def generate_sse_error_chunk(message: str, req_id: str, error_type: str = "server_error") -> str:
    """生成SSE错误块"""
    error_chunk = {"error": {"message": message, "type": error_type, "param": None, "code": req_id}}
    return f"data: {json.dumps(error_chunk)}\n\n"


# --- 流处理工具函数 ---
async def use_stream_response(req_id: str) -> AsyncGenerator[Any, None]:
    """使用流响应（从服务器的全局队列获取数据）"""
    from server import STREAM_QUEUE, logger
    import queue
    
    if STREAM_QUEUE is None:
        logger.warning(f"[{req_id}] STREAM_QUEUE is None, 无法使用流响应")
        return
    
    logger.info(f"[{req_id}] 开始使用流响应")
    
    empty_count = 0
    max_empty_retries = 300  # 30秒超时
    data_received = False
    
    try:
        while True:
            try:
                # 从队列中获取数据
                data = STREAM_QUEUE.get_nowait()
                if data is None:  # 结束标志
                    logger.info(f"[{req_id}] 接收到流结束标志")
                    break
                
                # 重置空计数器
                empty_count = 0
                data_received = True
                logger.debug(f"[{req_id}] 接收到流数据: {type(data)} - {str(data)[:200]}...")
                
                # 检查是否是JSON字符串形式的结束标志
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
                        # 如果不是JSON，直接返回字符串
                        logger.debug(f"[{req_id}] 返回非JSON字符串数据")
                        yield data
                else:
                    # 直接返回数据
                    yield data
                    
                    # 检查字典类型的结束标志
                    if isinstance(data, dict) and data.get("done") is True:
                        logger.info(f"[{req_id}] 接收到字典格式的完成标志")
                        break
                
            except (queue.Empty, asyncio.QueueEmpty):
                empty_count += 1
                if empty_count % 50 == 0:  # 每5秒记录一次等待状态
                    logger.info(f"[{req_id}] 等待流数据... ({empty_count}/{max_empty_retries})")
                
                if empty_count >= max_empty_retries:
                    if not data_received:
                        logger.error(f"[{req_id}] 流响应队列空读取次数达到上限且未收到任何数据，可能是辅助流未启动或出错")
                    else:
                        logger.warning(f"[{req_id}] 流响应队列空读取次数达到上限 ({max_empty_retries})，结束读取")
                    
                    # 返回超时完成信号，而不是简单退出
                    yield {"done": True, "reason": "internal_timeout", "body": "", "function": []}
                    return
                    
                await asyncio.sleep(0.1)  # 100ms等待
                continue
                
    except Exception as e:
        logger.error(f"[{req_id}] 使用流响应时出错: {e}")
        raise
    finally:
        logger.info(f"[{req_id}] 流响应使用完成，数据接收状态: {data_received}")


async def clear_stream_queue():
    """清空流队列（与原始参考文件保持一致）"""
    from server import STREAM_QUEUE, logger
    import queue

    if STREAM_QUEUE is None:
        logger.info("流队列未初始化或已被禁用，跳过清空操作。")
        return

    while True:
        try:
            data_chunk = await asyncio.to_thread(STREAM_QUEUE.get_nowait)
            # logger.info(f"清空流式队列缓存，丢弃数据: {data_chunk}")
        except queue.Empty:
            logger.info("流式队列已清空 (捕获到 queue.Empty)。")
            break
        except Exception as e:
            logger.error(f"清空流式队列时发生意外错误: {e}", exc_info=True)
            break
    logger.info("流式队列缓存清空完毕。")


# --- Helper response generator ---
async def use_helper_get_response(helper_endpoint: str, helper_sapisid: str) -> AsyncGenerator[str, None]:
    """使用Helper服务获取响应的生成器"""
    from server import logger
    import aiohttp

    logger.info(f"正在尝试使用Helper端点: {helper_endpoint}")

    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                'Content-Type': 'application/json',
                'Cookie': f'SAPISID={helper_sapisid}' if helper_sapisid else ''
            }
            
            async with session.get(helper_endpoint, headers=headers) as response:
                if response.status == 200:
                    async for chunk in response.content.iter_chunked(1024):
                        if chunk:
                            yield chunk.decode('utf-8', errors='ignore')
                else:
                    logger.error(f"Helper端点返回错误状态: {response.status}")
                    
    except Exception as e:
        logger.error(f"使用Helper端点时出错: {e}")


# --- 请求验证函数 ---
def validate_chat_request(messages: List[Message], req_id: str) -> Dict[str, Optional[str]]:
    """验证聊天请求"""
    from server import logger
    
    if not messages:
        raise ValueError(f"[{req_id}] 无效请求: 'messages' 数组缺失或为空。")
    
    if not any(msg.role != 'system' for msg in messages):
        raise ValueError(f"[{req_id}] 无效请求: 所有消息都是系统消息。至少需要一条用户或助手消息。")
    
    # 返回验证结果
    return {
        "error": None,
        "warning": None
    }


def _extension_for_mime(mime_type: str) -> str:
    """根据 MIME 类型返回合适的文件扩展名。未知类型返回 .bin"""
    mime_type = (mime_type or '').lower()
    mapping = {
        # images
        'image/png': '.png',
        'image/jpeg': '.jpg',
        'image/jpg': '.jpg',
        'image/gif': '.gif',
        'image/webp': '.webp',
        'image/svg+xml': '.svg',
        'image/bmp': '.bmp',
        # video
        'video/mp4': '.mp4',
        'video/webm': '.webm',
        'video/ogg': '.ogv',
        # audio
        'audio/mpeg': '.mp3',
        'audio/mp3': '.mp3',
        'audio/wav': '.wav',
        'audio/ogg': '.ogg',
        'audio/webm': '.weba',
        # documents
        'application/pdf': '.pdf',
        'application/zip': '.zip',
        'application/x-zip-compressed': '.zip',
        'application/json': '.json',
        'text/plain': '.txt',
        'text/markdown': '.md',
        'text/html': '.html',
    }
    return mapping.get(mime_type, f".{mime_type.split('/')[-1]}" if '/' in mime_type else '.bin')


def extract_data_url_to_local(data_url: str) -> Optional[str]:
    """
    解析并保存任意 data:URL (data:<mime>;base64,<payload>) 到本地文件，返回文件路径。
    支持图片、视频、音频、PDF 等常见类型。
    """
    from server import logger
    # 允许保存到通用上传目录
    output_dir = os.path.join(os.path.dirname(__file__), '..', 'upload_files')

    match = re.match(r"^data:(?P<mime>[^;]+);base64,(?P<data>.*)$", data_url)
    if not match:
        logger.error("错误: data:URL 格式不正确或不包含 base64 数据。")
        return None

    mime_type = match.group('mime')
    encoded_data = match.group('data')

    try:
        decoded_bytes = base64.b64decode(encoded_data)
    except base64.binascii.Error as e:
        logger.error(f"错误: Base64 解码失败 - {e}")
        return None

    md5_hash = hashlib.md5(decoded_bytes).hexdigest()
    file_extension = _extension_for_mime(mime_type)
    output_filepath = os.path.join(output_dir, f"{md5_hash}{file_extension}")

    # 每次处理前清理旧文件，确保目录为空
    try:
        if os.path.isdir(output_dir):
            for name in os.listdir(output_dir):
                try:
                    os.remove(os.path.join(output_dir, name))
                except Exception:
                    pass
    except Exception:
        pass
    os.makedirs(output_dir, exist_ok=True)

    if os.path.exists(output_filepath):
        logger.info(f"文件已存在，跳过保存: {output_filepath}")
        return output_filepath

    try:
        with open(output_filepath, 'wb') as f:
            f.write(decoded_bytes)
        logger.info(f"已保存 data:URL 到: {output_filepath}")
        return output_filepath
    except IOError as e:
        logger.error(f"错误: 保存文件失败 - {e}")
        return None


def save_blob_to_local(raw_bytes: bytes, mime_type: Optional[str] = None, fmt_ext: Optional[str] = None) -> Optional[str]:
    """将原始数据保存到 upload_files/ 下，按内容 MD5 命名，扩展名来源于 mime 或显式格式。"""
    from server import logger
    output_dir = os.path.join(os.path.dirname(__file__), '..', 'upload_files')
    md5_hash = hashlib.md5(raw_bytes).hexdigest()
    ext = None
    if fmt_ext:
        fmt_ext = fmt_ext.strip('. ')
        ext = f'.{fmt_ext}' if fmt_ext else None
    if not ext and mime_type:
        ext = _extension_for_mime(mime_type)
    if not ext:
        ext = '.bin'
    try:
        if os.path.isdir(output_dir):
            for name in os.listdir(output_dir):
                try:
                    os.remove(os.path.join(output_dir, name))
                except Exception:
                    pass
    except Exception:
        pass
    os.makedirs(output_dir, exist_ok=True)
    output_filepath = os.path.join(output_dir, f"{md5_hash}{ext}")
    if os.path.exists(output_filepath):
        logger.info(f"文件已存在，跳过保存: {output_filepath}")
        return output_filepath
    try:
        with open(output_filepath, 'wb') as f:
            f.write(raw_bytes)
        logger.info(f"已保存二进制到: {output_filepath}")
        return output_filepath
    except IOError as e:
        logger.error(f"错误: 保存二进制失败 - {e}")
        return None


# --- 提示准备函数 ---
def prepare_combined_prompt(messages: List[Message], req_id: str) -> Tuple[str, List[str]]:
    """准备组合提示"""
    from server import logger
    
    logger.info(f"[{req_id}] (准备提示) 正在从 {len(messages)} 条消息准备组合提示 (包括历史)。")
    # 清空上一请求的上传目录（按请求粒度），避免残留文件
    try:
        upload_dir = os.path.join(os.path.dirname(__file__), '..', 'upload_files')
        if os.path.isdir(upload_dir):
            for name in os.listdir(upload_dir):
                fp = os.path.join(upload_dir, name)
                try:
                    if os.path.isfile(fp):
                        os.remove(fp)
                except Exception:
                    pass
    except Exception:
        pass
    
    combined_parts = []
    system_prompt_content: Optional[str] = None
    processed_system_message_indices = set()
    files_list: List[str] = []  # 收集需要上传的本地文件路径（图片、视频、PDF等）

    # 处理系统消息
    for i, msg in enumerate(messages):
        if msg.role == 'system':
            content = msg.content
            if isinstance(content, str) and content.strip():
                system_prompt_content = content.strip()
                processed_system_message_indices.add(i)
                logger.info(f"[{req_id}] (准备提示) 在索引 {i} 找到并使用系统提示: '{system_prompt_content[:80]}...'")
                system_instr_prefix = "系统指令:\n"
                combined_parts.append(f"{system_instr_prefix}{system_prompt_content}")
            else:
                logger.info(f"[{req_id}] (准备提示) 在索引 {i} 忽略非字符串或空的系统消息。")
                processed_system_message_indices.add(i)
            break
    
    role_map_ui = {"user": "用户", "assistant": "助手", "system": "系统", "tool": "工具"}
    turn_separator = "\n---\n"
    
    # 处理其他消息
    for i, msg in enumerate(messages):
        if i in processed_system_message_indices:
            continue
        
        if msg.role == 'system':
            logger.info(f"[{req_id}] (准备提示) 跳过在索引 {i} 的后续系统消息。")
            continue
        
        if combined_parts:
            combined_parts.append(turn_separator)
        
        role = msg.role or 'unknown'
        role_prefix_ui = f"{role_map_ui.get(role, role.capitalize())}:\n"
        current_turn_parts = [role_prefix_ui]
        
        content = msg.content or ''
        content_str = ""
        
        if isinstance(content, str):
            content_str = content.strip()
        elif isinstance(content, list):
            # 处理多模态内容（更健壮地识别各类附件项）
            text_parts = []
            for item in content:
                # 统一获取项类型（可能缺失）
                item_type = None
                if hasattr(item, 'type'):
                    try:
                        item_type = item.type
                    except Exception:
                        item_type = None
                if item_type is None and isinstance(item, dict):
                    item_type = item.get('type')

                if item_type == 'text':
                    # 文本项
                    if hasattr(item, 'text'):
                        text_parts.append(getattr(item, 'text', '') or '')
                    elif isinstance(item, dict):
                        text_parts.append(item.get('text', ''))
                    continue

                # 图片/文件/媒体 URL 项（类型缺失时也尝试识别）
                if item_type in ('image_url', 'file_url', 'media_url', 'input_image') or (
                    isinstance(item, dict) and (
                        'image_url' in item or 'input_image' in item or 'file_url' in item or 'media_url' in item or 'url' in item
                    )
                ):
                    try:
                        url_value = None
                        # Pydantic 对象属性
                        if hasattr(item, 'image_url') and item.image_url:
                            url_value = item.image_url.url
                            try:
                                detail_val = getattr(item.image_url, 'detail', None)
                                if detail_val:
                                    text_parts.append(f"[图像细节: detail={detail_val}]")
                            except Exception:
                                pass
                        elif hasattr(item, 'input_image') and item.input_image:
                            url_value = item.input_image.url
                            try:
                                detail_val = getattr(item.input_image, 'detail', None)
                                if detail_val:
                                    text_parts.append(f"[图像细节: detail={detail_val}]")
                            except Exception:
                                pass
                        elif hasattr(item, 'file_url') and item.file_url:
                            url_value = item.file_url.url
                        elif hasattr(item, 'media_url') and item.media_url:
                            url_value = item.media_url.url
                        elif hasattr(item, 'url') and item.url:
                            url_value = item.url
                        # 字典结构
                        if url_value is None and isinstance(item, dict):
                            if isinstance(item.get('image_url'), dict):
                                url_value = item['image_url'].get('url')
                                detail_val = item['image_url'].get('detail')
                                if detail_val:
                                    text_parts.append(f"[图像细节: detail={detail_val}]")
                            elif isinstance(item.get('image_url'), str):
                                url_value = item.get('image_url')
                            elif isinstance(item.get('input_image'), dict):
                                url_value = item['input_image'].get('url')
                                detail_val = item['input_image'].get('detail')
                                if detail_val:
                                    text_parts.append(f"[图像细节: detail={detail_val}]")
                            elif isinstance(item.get('input_image'), str):
                                url_value = item.get('input_image')
                            elif isinstance(item.get('file_url'), dict):
                                url_value = item['file_url'].get('url')
                            elif isinstance(item.get('file_url'), str):
                                url_value = item.get('file_url')
                            elif isinstance(item.get('media_url'), dict):
                                url_value = item['media_url'].get('url')
                            elif isinstance(item.get('media_url'), str):
                                url_value = item.get('media_url')
                            elif 'url' in item:
                                url_value = item.get('url')
                            elif isinstance(item.get('file'), dict):
                                # 兼容通用 file 字段
                                url_value = item['file'].get('url') or item['file'].get('path')

                        url_value = (url_value or '').strip()
                        if not url_value:
                            continue

                        # 归一化到本地文件列表，并记录日志
                        if url_value.startswith('data:'):
                            file_path = extract_data_url_to_local(url_value)
                            if file_path:
                                files_list.append(file_path)
                                logger.info(f"[{req_id}] (准备提示) 已识别并加入 data:URL 附件: {file_path}")
                        elif url_value.startswith('file:'):
                            parsed = urlparse(url_value)
                            local_path = unquote(parsed.path)
                            if os.path.exists(local_path):
                                files_list.append(local_path)
                                logger.info(f"[{req_id}] (准备提示) 已识别并加入本地附件(file://): {local_path}")
                            else:
                                logger.warning(f"[{req_id}] (准备提示) file URL 指向的本地文件不存在: {local_path}")
                        elif os.path.isabs(url_value) and os.path.exists(url_value):
                            files_list.append(url_value)
                            logger.info(f"[{req_id}] (准备提示) 已识别并加入本地附件(绝对路径): {url_value}")
                        else:
                            logger.info(f"[{req_id}] (准备提示) 忽略非本地附件 URL: {url_value}")
                    except Exception as e:
                        logger.warning(f"[{req_id}] (准备提示) 处理附件 URL 时发生错误: {e}")
                    continue

                # 音/视频输入
                if item_type in ('input_audio', 'input_video'):
                    try:
                        inp = None
                        if hasattr(item, 'input_audio') and item.input_audio:
                            inp = item.input_audio
                        elif hasattr(item, 'input_video') and item.input_video:
                            inp = item.input_video
                        elif isinstance(item, dict):
                            inp = item.get('input_audio') or item.get('input_video')

                        if inp:
                            url_value = None
                            data_val = None
                            mime_val = None
                            fmt_val = None
                            if isinstance(inp, dict):
                                url_value = inp.get('url')
                                data_val = inp.get('data')
                                mime_val = inp.get('mime_type')
                                fmt_val = inp.get('format')
                            else:
                                url_value = getattr(inp, 'url', None)
                                data_val = getattr(inp, 'data', None)
                                mime_val = getattr(inp, 'mime_type', None)
                                fmt_val = getattr(inp, 'format', None)

                            if url_value:
                                if url_value.startswith('data:'):
                                    saved = extract_data_url_to_local(url_value)
                                    if saved:
                                        files_list.append(saved)
                                        logger.info(f"[{req_id}] (准备提示) 已识别并加入音视频 data:URL 附件: {saved}")
                                elif url_value.startswith('file:'):
                                    parsed = urlparse(url_value)
                                    local_path = unquote(parsed.path)
                                    if os.path.exists(local_path):
                                        files_list.append(local_path)
                                        logger.info(f"[{req_id}] (准备提示) 已识别并加入音视频本地附件(file://): {local_path}")
                                elif os.path.isabs(url_value) and os.path.exists(url_value):
                                    files_list.append(url_value)
                                    logger.info(f"[{req_id}] (准备提示) 已识别并加入音视频本地附件(绝对路径): {url_value}")
                            elif data_val:
                                if isinstance(data_val, str) and data_val.startswith('data:'):
                                    saved = extract_data_url_to_local(data_val)
                                    if saved:
                                        files_list.append(saved)
                                        logger.info(f"[{req_id}] (准备提示) 已识别并加入音视频 data:URL 附件: {saved}")
                                else:
                                    # 认为是纯 base64 数据
                                    try:
                                        raw = base64.b64decode(data_val)
                                        saved = save_blob_to_local(raw, mime_val, fmt_val)
                                        if saved:
                                            files_list.append(saved)
                                            logger.info(f"[{req_id}] (准备提示) 已识别并加入音视频 base64 附件: {saved}")
                                    except Exception:
                                        pass
                    except Exception as e:
                        logger.warning(f"[{req_id}] (准备提示) 处理音视频输入时出错: {e}")
                    continue

                # 其他未知项：记录而不影响
                logger.warning(f"[{req_id}] (准备提示) 警告: 在索引 {i} 的消息中忽略非文本或未知类型的 content item")
            content_str = "\n".join(text_parts).strip()
        elif isinstance(content, dict):
            # 兼容字典形式的内容，可能包含 'attachments'/'images'/'media'/'files'
            text_parts = []
            attachments_keys = ['attachments', 'images', 'media', 'files']
            for key in attachments_keys:
                items = content.get(key)
                if isinstance(items, list):
                    for it in items:
                        url_value = None
                        if isinstance(it, str):
                            url_value = it
                        elif isinstance(it, dict):
                            url_value = it.get('url') or it.get('path')
                            if not url_value and isinstance(it.get('image_url'), dict):
                                url_value = it['image_url'].get('url')
                            elif not url_value and isinstance(it.get('input_image'), dict):
                                url_value = it['input_image'].get('url')
                        url_value = (url_value or '').strip()
                        if not url_value:
                            continue
                        if url_value.startswith('data:'):
                            fp = extract_data_url_to_local(url_value)
                            if fp:
                                files_list.append(fp)
                                logger.info(f"[{req_id}] (准备提示) 已识别并加入字典附件 data:URL: {fp}")
                        elif url_value.startswith('file:'):
                            parsed = urlparse(url_value)
                            lp = unquote(parsed.path)
                            if os.path.exists(lp):
                                files_list.append(lp)
                                logger.info(f"[{req_id}] (准备提示) 已识别并加入字典附件 file://: {lp}")
                        elif os.path.isabs(url_value) and os.path.exists(url_value):
                            files_list.append(url_value)
                            logger.info(f"[{req_id}] (准备提示) 已识别并加入字典附件绝对路径: {url_value}")
                        else:
                            logger.info(f"[{req_id}] (准备提示) 忽略字典附件的非本地 URL: {url_value}")
            # 同时将字典中可能的纯文本说明拼入
            if isinstance(content.get('text'), str):
                text_parts.append(content.get('text'))
            content_str = "\n".join(text_parts).strip()
        else:
            logger.warning(f"[{req_id}] (准备提示) 警告: 角色 {role} 在索引 {i} 的内容类型意外 ({type(content)}) 或为 None。")
            content_str = str(content or "").strip()
        
        if content_str:
            current_turn_parts.append(content_str)
        
        # 处理工具调用
        tool_calls = msg.tool_calls
        if role == 'assistant' and tool_calls:
            if content_str:
                current_turn_parts.append("\n")
            
            tool_call_visualizations = []
            for tool_call in tool_calls:
                if hasattr(tool_call, 'type') and tool_call.type == 'function':
                    function_call = tool_call.function
                    func_name = function_call.name if function_call else None
                    func_args_str = function_call.arguments if function_call else None
                    
                    try:
                        parsed_args = json.loads(func_args_str if func_args_str else '{}')
                        formatted_args = json.dumps(parsed_args, indent=2, ensure_ascii=False)
                    except (json.JSONDecodeError, TypeError):
                        formatted_args = func_args_str if func_args_str is not None else "{}"
                    
                    tool_call_visualizations.append(
                        f"请求调用函数: {func_name}\n参数:\n{formatted_args}"
                    )
                    # 执行并附加结果到提示
                    try:
                        exec_result = execute_tool_call(func_name or '', func_args_str or '{}')
                        tool_call_visualizations.append(
                            f"函数执行结果:\n{exec_result}"
                        )
                    except Exception:
                        pass
            
            if tool_call_visualizations:
                current_turn_parts.append("\n".join(tool_call_visualizations))
        
        if len(current_turn_parts) > 1 or (role == 'assistant' and tool_calls):
            combined_parts.append("".join(current_turn_parts))
        elif not combined_parts and not current_turn_parts:
            logger.info(f"[{req_id}] (准备提示) 跳过角色 {role} 在索引 {i} 的空消息 (且无工具调用)。")
        elif len(current_turn_parts) == 1 and not combined_parts:
            logger.info(f"[{req_id}] (准备提示) 跳过角色 {role} 在索引 {i} 的空消息 (只有前缀)。")
    
    final_prompt = "".join(combined_parts)
    if final_prompt:
        final_prompt += "\n"
    
    preview_text = final_prompt[:300].replace('\n', '\\n')
    logger.info(f"[{req_id}] (准备提示) 组合提示长度: {len(final_prompt)}，附件数量: {len(files_list)}。预览: '{preview_text}...'")
    
    return final_prompt, files_list


def _extract_json_from_text(text: str) -> Optional[str]:
    """尝试从纯文本中提取首个 JSON 对象字符串。"""
    if not text:
        return None
    # 简单启发式：找到第一个 '{' 与最后一个匹配的 '}'
    try:
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            candidate = text[start:end+1].strip()
            json.loads(candidate)
            return candidate
    except Exception:
        return None
    return None


def _get_latest_user_text(messages: List[Message]) -> str:
    """提取最近一条用户消息的文本内容（拼接多段 text）。"""
    for msg in reversed(messages):
        if msg.role == 'user':
            content = msg.content
            if isinstance(content, str):
                return content
            elif isinstance(content, list):
                parts: List[str] = []
                for it in content:
                    if isinstance(it, dict) and it.get('type') == 'text':
                        parts.append(it.get('text') or '')
                    elif hasattr(it, 'type') and it.type == 'text':
                        parts.append(getattr(it, 'text', '') or '')
                return "\n".join(p for p in parts if p)
            else:
                return ''
    return ''


def maybe_execute_tools(messages: List[Message], tools: Optional[List[Dict[str, Any]]], tool_choice: Optional[Union[str, Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """
    基于 tools/tool_choice 的主动函数执行：
    - 若 tool_choice 指明函数名（字符串或 {type:'function', function:{name}}），则尝试执行该函数；
    - 若 tool_choice 为 'auto' 且仅提供一个工具，则执行该工具；
    - 参数来源：从最近一条用户消息的文本中尝试提取 JSON；若失败则使用空参数。
    - 返回 [{name, arguments, result}]；如无可执行则返回 None。
    """
    try:
        chosen_name: Optional[str] = None
        if isinstance(tool_choice, dict):
            fn = tool_choice.get('function') if tool_choice else None
            if isinstance(fn, dict):
                chosen_name = fn.get('name')
        elif isinstance(tool_choice, str):
            lc = tool_choice.lower()
            if lc in ('none', 'no', 'off'):
                return None
            if lc in ('auto', 'required', 'any'):
                if isinstance(tools, list) and len(tools) == 1:
                    chosen_name = tools[0].get('function', {}).get('name') or tools[0].get('name')
            else:
                chosen_name = tool_choice
        elif tool_choice is None:
            # 不主动执行
            return None

        if not chosen_name:
            return None

        user_text = _get_latest_user_text(messages)
        args_json = _extract_json_from_text(user_text) or '{}'
        result_str = execute_tool_call(chosen_name, args_json)
        return [{"name": chosen_name, "arguments": args_json, "result": result_str}]
    except Exception:
        return None


def estimate_tokens(text: str) -> int:
    """
    估算文本的token数量
    使用简单的字符计数方法：
    - 英文：大约4个字符 = 1个token
    - 中文：大约1.5个字符 = 1个token  
    - 混合文本：采用加权平均
    """
    if not text:
        return 0
    
    # 统计中文字符数量（包括中文标点）
    chinese_chars = sum(1 for char in text if '\u4e00' <= char <= '\u9fff' or '\u3000' <= char <= '\u303f' or '\uff00' <= char <= '\uffef')
    
    # 统计非中文字符数量
    non_chinese_chars = len(text) - chinese_chars
    
    # 计算token估算
    chinese_tokens = chinese_chars / 1.5  # 中文大约1.5字符/token
    english_tokens = non_chinese_chars / 4.0  # 英文大约4字符/token
    
    return max(1, int(chinese_tokens + english_tokens))


def calculate_usage_stats(messages: List[dict], response_content: str, reasoning_content: str = None) -> dict:
    """
    计算token使用统计
    
    Args:
        messages: 请求中的消息列表
        response_content: 响应内容
        reasoning_content: 推理内容（可选）
    
    Returns:
        包含token使用统计的字典
    """
    # 计算输入token（prompt tokens）
    prompt_text = ""
    for message in messages:
        role = message.get("role", "")
        content = message.get("content", "")
        prompt_text += f"{role}: {content}\n"
    
    prompt_tokens = estimate_tokens(prompt_text)
    
    # 计算输出token（completion tokens）
    completion_text = response_content or ""
    if reasoning_content:
        completion_text += reasoning_content
    
    completion_tokens = estimate_tokens(completion_text)
    
    # 总token数
    total_tokens = prompt_tokens + completion_tokens
    
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens
    } 


def generate_sse_stop_chunk_with_usage(req_id: str, model: str, usage_stats: dict, reason: str = "stop") -> str:
    """生成带usage统计的SSE停止块"""
    return generate_sse_stop_chunk(req_id, model, reason, usage_stats) 
