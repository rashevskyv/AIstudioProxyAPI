import json
import time
import os
from typing import Any, Dict, List, Optional, Set


def tool_get_current_time(params: Dict[str, Any]) -> Dict[str, Any]:
    return {"current_time": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}


def tool_echo(params: Dict[str, Any]) -> Dict[str, Any]:
    return {"echo": params}


def tool_sum(params: Dict[str, Any]) -> Dict[str, Any]:
    values = params.get("values")
    if isinstance(values, list):
        try:
            total = sum(float(v) for v in values)
        except Exception:
            total = None
    else:
        total = None
    return {"sum": total, "count": len(values) if isinstance(values, list) else 0}


FUNCTION_REGISTRY = {
    "get_current_time": tool_get_current_time,
    "echo": tool_echo,
    "sum": tool_sum,
}

# Runtime-allowed tool names from incoming requests (OpenAI tools array)
_ALLOWED_RUNTIME_TOOLS: Set[str] = set()
_RUNTIME_MCP_ENDPOINT: Optional[str] = None

def register_runtime_tools(tools: Optional[List[Dict[str, Any]]], mcp_endpoint: Optional[str] = None) -> None:
    """Register tool names declared in the request as allowed.
    The server may delegate unknown tools to MCP if configured.
    """
    # Reset per-request registry to avoid leakage across requests
    global _RUNTIME_MCP_ENDPOINT
    _ALLOWED_RUNTIME_TOOLS.clear()
    _RUNTIME_MCP_ENDPOINT = None
    if not tools:
        return
    try:
        for t in tools:
            name = None
            if isinstance(t, dict):
                fn = t.get('function') if 'function' in t else t
                if isinstance(fn, dict):
                    name = fn.get('name') or t.get('name')
                else:
                    name = t.get('name')
            if name:
                _ALLOWED_RUNTIME_TOOLS.add(str(name))
            # Detect per-tool endpoint extension
            ext_ep = None
            if isinstance(t, dict):
                ext_ep = (
                    t.get('x-mcp-endpoint')
                    or t.get('x_mcp_endpoint')
                    or (isinstance(t.get('function'), dict) and t['function'].get('x-mcp-endpoint'))
                    or None
                )
            if ext_ep and not mcp_endpoint:
                mcp_endpoint = ext_ep
        # Capture per-request MCP endpoint if provided (explicit or via tool extension)
        if mcp_endpoint:
            _RUNTIME_MCP_ENDPOINT = mcp_endpoint
    except Exception:
        # be forgiving on malformed tools
        pass


async def execute_tool_call(name: str, arguments_json: str) -> str:
    """执行注册的工具并返回字符串化结果。未知工具返回描述性错误。
    完全异步：内置函数直接执行；MCP 路径使用异步 httpx 客户端。
    """
    try:
        params = json.loads(arguments_json or "{}")
    except Exception:
        params = {}

    func = FUNCTION_REGISTRY.get(name)
    if not func:
        # If tool is not built-in but declared, try MCP adapter if configured (env or per-request)
        if name in _ALLOWED_RUNTIME_TOOLS:
            try:
                from .mcp_adapter import execute_mcp_tool, execute_mcp_tool_with_endpoint
                if _RUNTIME_MCP_ENDPOINT:
                    return await execute_mcp_tool_with_endpoint(_RUNTIME_MCP_ENDPOINT, name, params)
                if os.environ.get('MCP_HTTP_ENDPOINT'):
                    return await execute_mcp_tool(name, params)
            except Exception as e:
                return json.dumps({"error": f"MCP execution failed: {e}"}, ensure_ascii=False)
        return json.dumps({"error": f"Unknown tool: {name}", "arguments": params}, ensure_ascii=False)

    try:
        result = func(params)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Execution failed: {e}"}, ensure_ascii=False)
