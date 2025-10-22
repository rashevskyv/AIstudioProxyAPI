import json
import os
from typing import Dict, Any
import asyncio

import httpx


def _normalize_endpoint(ep: str) -> str:
    if not ep:
        raise RuntimeError('MCP HTTP endpoint not provided')
    return ep.rstrip('/')


async def execute_mcp_tool(name: str, params: Dict[str, Any]) -> str:
    """
    Minimal MCP-over-HTTP adapter:
    - POST {MCP_HTTP_ENDPOINT}/tools/execute with {name, arguments}
    - Returns JSON string.
    Compatible with servers exposing MCP-like HTTP interface.
    """
    ep = os.environ.get('MCP_HTTP_ENDPOINT')
    if not ep:
        raise RuntimeError('MCP_HTTP_ENDPOINT not configured')
    url = f"{_normalize_endpoint(ep)}/tools/execute"
    payload = {"name": name, "arguments": params}
    headers = {"Content-Type": "application/json"}
    timeout = float(os.environ.get('MCP_HTTP_TIMEOUT', '15'))
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}
    return json.dumps(data, ensure_ascii=False)


async def execute_mcp_tool_with_endpoint(endpoint: str, name: str, params: Dict[str, Any]) -> str:
    url = f"{_normalize_endpoint(endpoint)}/tools/execute"
    payload = {"name": name, "arguments": params}
    headers = {"Content-Type": "application/json"}
    timeout = float(os.environ.get('MCP_HTTP_TIMEOUT', '15'))
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}
    return json.dumps(data, ensure_ascii=False)


# Synchronous helpers for use inside threads
def execute_mcp_tool_sync(name: str, params: Dict[str, Any]) -> str:
    ep = os.environ.get('MCP_HTTP_ENDPOINT')
    if not ep:
        raise RuntimeError('MCP_HTTP_ENDPOINT not configured')
    url = f"{_normalize_endpoint(ep)}/tools/execute"
    payload = {"name": name, "arguments": params}
    headers = {"Content-Type": "application/json"}
    timeout = float(os.environ.get('MCP_HTTP_TIMEOUT', '15'))
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}
    return json.dumps(data, ensure_ascii=False)


def execute_mcp_tool_with_endpoint_sync(endpoint: str, name: str, params: Dict[str, Any]) -> str:
    url = f"{_normalize_endpoint(endpoint)}/tools/execute"
    payload = {"name": name, "arguments": params}
    headers = {"Content-Type": "application/json"}
    timeout = float(os.environ.get('MCP_HTTP_TIMEOUT', '15'))
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}
    return json.dumps(data, ensure_ascii=False)
