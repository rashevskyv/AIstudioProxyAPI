import json
import time
from typing import Any, Dict


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


def execute_tool_call(name: str, arguments_json: str) -> str:
    """执行注册的工具并返回字符串化结果。未知工具返回描述性错误。"""
    try:
        params = json.loads(arguments_json or "{}")
    except Exception:
        params = {}

    func = FUNCTION_REGISTRY.get(name)
    if not func:
        return json.dumps({"error": f"Unknown tool: {name}", "arguments": params}, ensure_ascii=False)

    try:
        result = func(params)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Execution failed: {e}"}, ensure_ascii=False)

