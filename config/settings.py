"""
主要设置配置模块
包含环境变量配置、路径配置、代理配置等运行时设置
"""

import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# --- 全局日志控制配置 ---
DEBUG_LOGS_ENABLED = os.environ.get('DEBUG_LOGS_ENABLED', 'false').lower() in ('true', '1', 'yes')
TRACE_LOGS_ENABLED = os.environ.get('TRACE_LOGS_ENABLED', 'false').lower() in ('true', '1', 'yes')

# --- 认证相关配置 ---
AUTO_SAVE_AUTH = os.environ.get('AUTO_SAVE_AUTH', '').lower() in ('1', 'true', 'yes')
AUTH_SAVE_TIMEOUT = int(os.environ.get('AUTH_SAVE_TIMEOUT', '30'))
AUTO_CONFIRM_LOGIN = os.environ.get('AUTO_CONFIRM_LOGIN', 'true').lower() in ('1', 'true', 'yes')

# --- 路径配置 ---
AUTH_PROFILES_DIR = os.path.join(os.path.dirname(__file__), '..', 'auth_profiles')
ACTIVE_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, 'active')
SAVED_AUTH_DIR = os.path.join(AUTH_PROFILES_DIR, 'saved')
LOG_DIR = os.path.join(os.path.dirname(__file__), '..', 'logs')
APP_LOG_FILE_PATH = os.path.join(LOG_DIR, 'app.log')
UPLOAD_FILES_DIR = os.path.join(os.path.dirname(__file__), '..', 'upload_files')

def get_environment_variable(key: str, default: str = '') -> str:
    """获取环境变量值"""
    return os.environ.get(key, default)

def get_boolean_env(key: str, default: bool = False) -> bool:
    """获取布尔型环境变量"""
    value = os.environ.get(key, '').lower()
    if default:
        return value not in ('false', '0', 'no', 'off')
    else:
        return value in ('true', '1', 'yes', 'on')

def get_int_env(key: str, default: int = 0) -> int:
    """获取整型环境变量"""
    try:
        return int(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default

# --- 代理配置 ---
# 注意：代理配置现在在 api_utils/app.py 中动态设置，根据 STREAM_PORT 环境变量决定
NO_PROXY_ENV = os.environ.get('NO_PROXY')

# --- 脚本注入配置 ---
ENABLE_SCRIPT_INJECTION = get_boolean_env('ENABLE_SCRIPT_INJECTION', True)
ONLY_COLLECT_CURRENT_USER_ATTACHMENTS = get_boolean_env('ONLY_COLLECT_CURRENT_USER_ATTACHMENTS', False)
USERSCRIPT_PATH = get_environment_variable('USERSCRIPT_PATH', 'browser_utils/more_modles.js')
# 注意：MODEL_CONFIG_PATH 已废弃，现在直接从油猴脚本解析模型数据
