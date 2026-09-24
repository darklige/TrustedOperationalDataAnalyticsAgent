"""Conservative, provider-aware input sizing for a bounded model context.

This counts UTF-8 bytes of the serialized request instead of assuming a
tokenizer shared by every compatible gateway. A byte bound is intentionally
conservative for byte-based subword tokenizers. The model window also reserves
space for its output and a protocol margin; reported usage is recorded after
the call so estimate drift can be audited.
"""

from __future__ import annotations

import json
from typing import Any

from .chat_provider import ChatCompletionsProvider
from .provider import ModelProvider


def estimate_input_tokens(provider: ModelProvider, messages: list[dict[str, Any]],
                          tools: list[dict[str, Any]], instructions: str) -> int:
    """Upper-bound request text using bytes plus per-item framing allowance."""
    if isinstance(provider, ChatCompletionsProvider):
        payload: dict[str, Any] = {
            "messages": provider.to_chat_messages(messages, instructions),
            "tools": [provider._chat_tool(spec) for spec in tools],
        }
    else:
        payload = {"input": messages, "instructions": instructions, "tools": tools}
    rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                          default=str).encode("utf-8")
    return len(rendered) + 8 * (len(messages) + len(tools) + 1)
