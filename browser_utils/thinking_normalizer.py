"""
Thinking Mode Parameter Normalization Module
Converts reasoning_effort parameters to standardized thinking directives

This module is responsible for converting various formats of reasoning_effort parameters into unified internal directive structures.
"""

from typing import Optional, Any, Dict
from dataclasses import dataclass
from config import ENABLE_THINKING_BUDGET, DEFAULT_THINKING_BUDGET


@dataclass
class ThinkingDirective:
    """Standardized thinking directive

    Attributes:
        thinking_enabled: Whether to enable thinking mode (master switch)
        budget_enabled: Whether to limit thinking budget
        budget_value: Budget token count (only valid when budget_enabled=True)
        original_value: Original reasoning_effort value (for logging)
    """
    thinking_enabled: bool
    budget_enabled: bool
    budget_value: Optional[int]
    original_value: Any


def normalize_reasoning_effort(reasoning_effort: Optional[Any]) -> ThinkingDirective:
    """Normalize reasoning_effort parameter to standardized thinking directive

    Args:
        reasoning_effort: reasoning_effort parameter in API request, possible values:
            - None: Use default configuration
            - 0 or "0": Disable thinking mode
            - Positive integer: Enable thinking, set specific budget value
            - "low"/"medium"/"high": Enable thinking, use preset budget
            - "none" or "-1" or -1: Enable thinking, no budget limit

    Returns:
        ThinkingDirective: Standardized thinking directive

    Examples:
        >>> normalize_reasoning_effort(None)
        ThinkingDirective(thinking_enabled=False, budget_enabled=False, budget_value=None, ...)

        >>> normalize_reasoning_effort(0)
        ThinkingDirective(thinking_enabled=False, budget_enabled=False, budget_value=None, ...)

        >>> normalize_reasoning_effort("medium")
        ThinkingDirective(thinking_enabled=True, budget_enabled=True, budget_value=8000, ...)

        >>> normalize_reasoning_effort("none")
        ThinkingDirective(thinking_enabled=True, budget_enabled=False, budget_value=None, ...)
    """

    # Scenario 1: User didn't specify, use default configuration
    if reasoning_effort is None:
        return ThinkingDirective(
            thinking_enabled=ENABLE_THINKING_BUDGET,
            budget_enabled=ENABLE_THINKING_BUDGET,
            budget_value=DEFAULT_THINKING_BUDGET if ENABLE_THINKING_BUDGET else None,
            original_value=None
        )

    # Scenario 2: Disable thinking mode (reasoning_effort = 0 or "0")
    if reasoning_effort == 0 or (isinstance(reasoning_effort, str) and reasoning_effort.strip() == "0"):
        return ThinkingDirective(
            thinking_enabled=False,
            budget_enabled=False,
            budget_value=None,
            original_value=reasoning_effort
        )

    # Scenario 3: Enable thinking but don't limit budget (reasoning_effort = "none" / "-1" / -1)
    if isinstance(reasoning_effort, str):
        reasoning_str = reasoning_effort.strip().lower()
        if reasoning_str in ["none", "-1"]:
            return ThinkingDirective(
                thinking_enabled=True,
                budget_enabled=False,
                budget_value=None,
                original_value=reasoning_effort
            )
    elif reasoning_effort == -1:
        return ThinkingDirective(
            thinking_enabled=True,
            budget_enabled=False,
            budget_value=None,
            original_value=reasoning_effort
        )

    # Scenario 4: Enable thinking and limit budget (specific numbers or preset values)
    budget_value = _parse_budget_value(reasoning_effort)

    if budget_value is not None and budget_value > 0:
        return ThinkingDirective(
            thinking_enabled=True,
            budget_enabled=True,
            budget_value=budget_value,
            original_value=reasoning_effort
        )

    # Invalid value: use default configuration
    return ThinkingDirective(
        thinking_enabled=ENABLE_THINKING_BUDGET,
        budget_enabled=ENABLE_THINKING_BUDGET,
        budget_value=DEFAULT_THINKING_BUDGET if ENABLE_THINKING_BUDGET else None,
        original_value=reasoning_effort
    )


def _parse_budget_value(reasoning_effort: Any) -> Optional[int]:
    """Parse budget value

    Args:
        reasoning_effort: reasoning_effort parameter value

    Returns:
        int: Budget token count, returns None if unable to parse
    """
    # If it's an integer, return directly
    if isinstance(reasoning_effort, int) and reasoning_effort > 0:
        return reasoning_effort

    # If it's a string, try to match preset values or parse as number
    if isinstance(reasoning_effort, str):
        effort_str = reasoning_effort.strip().lower()

        # Preset value mapping
        effort_map = {
            "low": 1000,
            "medium": 8000,
            "high": 24000,
        }

        # First try preset values
        if effort_str in effort_map:
            return effort_map[effort_str]

        # Then try to parse as number
        try:
            value = int(effort_str)
            if value > 0:
                return value
        except (ValueError, TypeError):
            pass

    return None


def format_directive_log(directive: ThinkingDirective) -> str:
    """Format thinking directive as log string

    Args:
        directive: Thinking directive

    Returns:
        str: Formatted log string
    """
    if not directive.thinking_enabled:
        return f"Disable thinking mode (original value: {directive.original_value})"
    elif directive.budget_enabled and directive.budget_value is not None:
        return f"Enable thinking with budget limit: {directive.budget_value} tokens (original value: {directive.original_value})"
    else:
        return f"Enable thinking without budget limit (original value: {directive.original_value})"
