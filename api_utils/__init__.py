"""
API工具模块
提供FastAPI应用初始化、路由处理和工具函数
"""

# 应用初始化
from .app import (
    create_app
)

# 路由处理器（改为从 routers 聚合导入）
from .routers import (
    read_index,
    get_css,
    get_js,
    get_api_info,
    health_check,
    list_models,
    chat_completions,
    cancel_request,
    get_queue_status,
    websocket_log_endpoint
)

# 工具函数
from .utils import (
    generate_sse_chunk,
    generate_sse_stop_chunk,
    generate_sse_error_chunk,
    use_stream_response,
    clear_stream_queue,
    use_helper_get_response,
    validate_chat_request,
    prepare_combined_prompt,
    estimate_tokens,
    calculate_usage_stats
)

# 请求处理器
from .request_processor import (
    _process_request_refactored
)

# 队列工作器
from .queue_worker import (
    queue_worker
)

__all__ = [
    # 应用初始化
    'create_app',
    # 路由处理器
    'read_index',
    'get_css',
    'get_js',
    'get_api_info',
    'health_check',
    'list_models',
    'chat_completions',
    'cancel_request',
    'get_queue_status',
    'websocket_log_endpoint',
    # 工具函数
    'generate_sse_chunk',
    'generate_sse_stop_chunk',
    'generate_sse_error_chunk',
    'use_stream_response',
    'clear_stream_queue',
    'use_helper_get_response',
    'validate_chat_request',
    'prepare_combined_prompt',
    'estimate_tokens',
    'calculate_usage_stats',
    # 请求处理器
    '_process_request_refactored',
    # 队列工作器
    'queue_worker'
] 
