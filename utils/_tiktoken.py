"""Token counting utilities backed by tiktoken."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sparkos.infrastructure.llm.models import ChatMessage

if TYPE_CHECKING:
    import tiktoken

_TOKENIZER_CACHE: dict[str, tiktoken.Encoding | None] = {}


def _get_encoding(model: str) -> tiktoken.Encoding | None:
    """Return a tiktoken encoding for *model*, or ``None`` on failure."""
    import tiktoken

    if model not in _TOKENIZER_CACHE:
        try:
            _TOKENIZER_CACHE[model] = tiktoken.encoding_for_model(model)
        except KeyError:
            _TOKENIZER_CACHE[model] = tiktoken.get_encoding("cl100k_base")
    return _TOKENIZER_CACHE[model]


def count_text(text: str, model: str = "gpt-4o") -> int:
    """Count the number of tokens in *text*."""
    encoding = _get_encoding(model)
    if encoding is None:
        return len(text) // 4  # rough fallback
    return len(encoding.encode(text))


def count_messages(messages: list[ChatMessage], model: str = "gpt-4o") -> int:
    """Count the number of tokens in a list of ChatMessages.

    Uses the same overhead as ``tiktoken``'s ``num_tokens_from_messages``.
    """
    encoding = _get_encoding(model)
    if encoding is None:
        return sum(count_text(m.content or "", model) for m in messages)

    if model.startswith(("gpt-3.5-turbo", "gpt-4", "gpt-4o")):
        tokens_per_message = 3
    else:
        tokens_per_message = 3

    total = 0
    for message in messages:
        total += tokens_per_message
        total += count_text(message.content or "", model)
        if message.tool_calls:
            for call in message.tool_calls:
                total += count_text(str(call), model)
        if message.tool_call_id:
            total += count_text(message.tool_call_id, model)
    total += 3  # every reply is primed with <|start|>assistant<|message|>
    return total


__all__ = ["count_messages", "count_text"]
