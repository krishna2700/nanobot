"""Parsers for non-standard tool-call formats embedded in LLM text content.

Some models (e.g. meituan LongCat) return tool calls wrapped in custom XML
tags inside the assistant message *content* instead of using the standard
OpenAI ``tool_calls`` field.  The helpers here extract those calls so the
rest of the agent loop can treat them identically to native tool calls.
"""

from __future__ import annotations

import re
from typing import Any

import json_repair

from nanobot.providers.base import LLMResponse, ToolCallRequest

# ---------------------------------------------------------------------------
# LongCat: <longcat_tool_call>{"name": ..., "arguments": ...}</longcat_tool_call>
# ---------------------------------------------------------------------------

_LONGCAT_RE = re.compile(
    r"<longcat_tool_call>\s*(.*?)\s*</longcat_tool_call>",
    re.DOTALL,
)


def _parse_longcat_block(raw: str, index: int) -> ToolCallRequest | None:
    """Parse a single ``<longcat_tool_call>`` JSON block.

    Returns *None* when the block cannot be interpreted as a valid tool call.
    """
    try:
        obj: dict[str, Any] = json_repair.loads(raw)
    except Exception:
        return None

    name = obj.get("name")
    arguments = obj.get("arguments")
    if not name or not isinstance(name, str):
        return None
    if arguments is None:
        arguments = {}
    if isinstance(arguments, str):
        try:
            arguments = json_repair.loads(arguments)
        except Exception:
            arguments = {}

    return ToolCallRequest(
        id=f"longcat_call_{index}",
        name=name,
        arguments=arguments if isinstance(arguments, dict) else {},
    )


def extract_longcat_tool_calls(content: str) -> tuple[list[ToolCallRequest], str | None]:
    """Extract ``<longcat_tool_call>`` blocks from *content*.

    Returns:
        A tuple of (tool_calls, remaining_content).
        *remaining_content* is the text with all matched tags removed and
        stripped; it becomes ``None`` when nothing meaningful remains.
    """
    matches = list(_LONGCAT_RE.finditer(content))
    if not matches:
        return [], content

    tool_calls: list[ToolCallRequest] = []
    for idx, m in enumerate(matches):
        tc = _parse_longcat_block(m.group(1), idx)
        if tc is not None:
            tool_calls.append(tc)

    # Remove matched tags from content
    remaining = _LONGCAT_RE.sub("", content).strip()
    return tool_calls, remaining or None


# ---------------------------------------------------------------------------
# Public post-processor — call after building an LLMResponse
# ---------------------------------------------------------------------------

def maybe_extract_content_tool_calls(response: LLMResponse) -> LLMResponse:
    """Post-process an ``LLMResponse``: if it has no native tool calls but
    the content contains recognised XML tool-call tags, extract them.

    The response is mutated in-place *and* returned for convenience.
    """
    if response.tool_calls:
        # Already has native tool calls — nothing to do.
        return response

    if not response.content:
        return response

    # Try LongCat format
    tool_calls, remaining = extract_longcat_tool_calls(response.content)
    if tool_calls:
        response.tool_calls = tool_calls
        response.content = remaining
        response.finish_reason = "tool_calls"

    return response
