from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

import httpx

from mio_core import __version__
from mio_core.config import Settings
from mio_core.providers.base import ChatChunk, LLMProvider, ProviderCapabilities


class KimiCodingProvider(LLMProvider):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._global = asyncio.Semaphore(settings.llm_global_concurrency)
        self._users: defaultdict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(1))
        self._capabilities: ProviderCapabilities | None = None

    @property
    def endpoint(self) -> str:
        return f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"mio-core/{__version__}",
        }

    async def health(self) -> bool:
        if not self.settings.llm_api_key:
            return False
        try:
            async for _chunk in self.stream_chat(
                [{"role": "user", "content": "Reply with OK."}], [], "_health"
            ):
                return True
        except (httpx.HTTPError, asyncio.TimeoutError):
            return False
        return False

    async def capabilities(self) -> ProviderCapabilities:
        if self._capabilities is None:
            if not self.settings.llm_api_key:
                self._capabilities = ProviderCapabilities(
                    streaming=False, tool_calls=False, model=self.settings.llm_model
                )
                return self._capabilities
            body = {
                "model": self.settings.llm_model,
                "messages": [
                    {
                        "role": "user",
                        "content": "Capability probe: reply OK. Do not call any tool.",
                    }
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "mio_noop",
                            "description": "No-op used only to test protocol acceptance.",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                "max_tokens": 2,
                "stream": False,
            }
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=8)) as client:
                    response = await client.post(self.endpoint, headers=self._headers(), json=body)
                self._capabilities = ProviderCapabilities(
                    streaming=True,
                    tool_calls=response.status_code < 400,
                    model=self.settings.llm_model,
                )
            except httpx.HTTPError:
                self._capabilities = ProviderCapabilities(
                    streaming=True, tool_calls=False, model=self.settings.llm_model
                )
        return self._capabilities

    async def stream_chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], user_id: str
    ) -> AsyncIterator[ChatChunk]:
        if not self.settings.llm_api_key:
            raise RuntimeError("MIO_LLM_API_KEY 尚未配置")
        body: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        timeout = httpx.Timeout(self.settings.llm_timeout_seconds, connect=15)
        async with self._global, self._users[user_id]:
            async with httpx.AsyncClient(timeout=timeout) as client:
                for attempt in range(4):
                    tool_calls: dict[int, dict[str, Any]] = {}
                    async with client.stream(
                        "POST", self.endpoint, headers=self._headers(), json=body
                    ) as response:
                        if response.status_code == 429 and attempt < 3:
                            retry_after = min(float(response.headers.get("retry-after", "1")), 15)
                            await response.aread()
                            await asyncio.sleep(retry_after * (2**attempt))
                            continue
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                if tool_calls:
                                    yield ChatChunk(tool_calls=list(tool_calls.values()))
                                return
                            event = json.loads(data)
                            choice = (event.get("choices") or [{}])[0]
                            delta = choice.get("delta") or {}
                            usage = event.get("usage")
                            for partial in delta.get("tool_calls") or []:
                                index = int(partial.get("index", 0))
                                current = tool_calls.setdefault(
                                    index,
                                    {
                                        "id": partial.get("id") or "",
                                        "type": "function",
                                        "function": {"name": "", "arguments": ""},
                                    },
                                )
                                if partial.get("id"):
                                    current["id"] = partial["id"]
                                function = partial.get("function") or {}
                                current["function"]["name"] += function.get("name") or ""
                                current["function"]["arguments"] += (
                                    function.get("arguments") or ""
                                )
                            yield ChatChunk(
                                text=delta.get("content") or "",
                                finish_reason=choice.get("finish_reason"),
                                usage=usage,
                            )
                        return
