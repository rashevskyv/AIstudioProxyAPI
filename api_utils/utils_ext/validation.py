from typing import Dict, Optional, List
from models import Message


def validate_chat_request(messages: List[Message], req_id: str) -> Dict[str, Optional[str]]:
    from server import logger

    if not messages:
        raise ValueError(f"[{req_id}] Invalid request: 'messages' array is missing or empty.")

    if not any(msg.role != 'system' for msg in messages):
        raise ValueError(f"[{req_id}] Invalid request: all messages are system messages. At least one user or assistant message is required.")

    return {"error": None, "warning": None}

