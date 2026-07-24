import asyncio

import httpx

from mio_core.config import Settings
from mio_core.providers.deepseek import DeepSeekProvider


def test_deepseek_stream_parses_openai_events(monkeypatch, tmp_path):
    async def handler(request: httpx.Request):
        assert request.headers["user-agent"].startswith("mio-core/")
        assert request.url == "https://api.deepseek.com/chat/completions"
        assert b'"model":"deepseek-v4-pro"' in request.content
        content = (
            'data: {"choices":[{"delta":{"content":"水"},"finish_reason":null}]}\n\n'
            'data: {"choices":[{"delta":{"content":"音"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, content=content)

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=transport, **kwargs),
    )
    provider = DeepSeekProvider(
        Settings(
            data_dir=tmp_path / "data",
            workspaces_dir=tmp_path / "workspaces",
            database_url=f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}",
            llm_api_key="test-key",
        )
    )
    async def collect():
        return [
            chunk.text
            async for chunk in provider.stream_chat(
                [{"role": "user", "content": "hi"}],
                [],
                "u1",
                "deepseek-v4-pro",
            )
        ]

    chunks = asyncio.run(collect())
    assert "".join(chunks) == "水音"


def test_deepseek_stream_reassembles_tool_calls(monkeypatch, tmp_path):
    async def handler(_request: httpx.Request):
        content = (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
            '"function":{"name":"get_job_","arguments":"{\\\"job_id\\\":"}}]}}]}\n\n'
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"name":"status","arguments":"\\\"abc\\\"}"}}]},'
            '"finish_reason":"tool_calls"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, content=content)

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=transport, **kwargs),
    )
    provider = DeepSeekProvider(
        Settings(
            data_dir=tmp_path / "data",
            workspaces_dir=tmp_path / "workspaces",
            database_url=f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}",
            llm_api_key="test-key",
        )
    )

    async def collect():
        return [
            chunk
            async for chunk in provider.stream_chat(
                [{"role": "user", "content": "status"}], [], "u1"
            )
        ]

    calls = [call for chunk in asyncio.run(collect()) for call in (chunk.tool_calls or [])]
    assert calls[0]["function"]["name"] == "get_job_status"
    assert calls[0]["function"]["arguments"] == '{"job_id":"abc"}'
