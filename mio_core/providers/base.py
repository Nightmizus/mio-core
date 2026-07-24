from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ProviderCapabilities:
    streaming: bool
    tool_calls: bool
    model: str


@dataclass(slots=True)
class ChatChunk:
    text: str = ""
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
    tool_calls: list[dict[str, Any]] | None = None


class LLMProvider(ABC):
    @abstractmethod
    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        user_id: str,
        model: str | None = None,
    ) -> AsyncIterator[ChatChunk]:
        raise NotImplementedError

    @abstractmethod
    async def health(self, model: str | None = None) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def capabilities(self, model: str | None = None) -> ProviderCapabilities:
        raise NotImplementedError
