import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, sendChat, setCsrf } from "./api";

describe("api client", () => {
  beforeEach(() => {
    setCsrf("");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("adds JSON and CSRF headers to state-changing requests", async () => {
    setCsrf("csrf-token");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await api("/api/example", {
      method: "POST",
      body: JSON.stringify({ value: 1 })
    });

    expect(fetchMock).toHaveBeenCalledOnce();
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-token");
    expect(init.credentials).toBe("same-origin");
  });

  it("surfaces API error details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "登录失败" }), {
          status: 401,
          headers: { "Content-Type": "application/json" }
        })
      )
    );

    await expect(api("/api/auth/me")).rejects.toThrow("登录失败");
  });

  it("parses streamed chat deltas", async () => {
    setCsrf("csrf-token");
    const encoder = new TextEncoder();
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'data: {"type":"delta","text":"你"}\n\n' +
              'data: {"type":"delta","text":"好"}\n\n' +
              'data: {"type":"done"}\n\n'
          )
        );
        controller.close();
      }
    });
    const fetchMock = vi.fn().mockResolvedValue(new Response(body, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const deltas: string[] = [];

    await sendChat(
      "conversation-1",
      "你好",
      "deepseek-v4-pro",
      (text) => deltas.push(text)
    );

    expect(deltas).toEqual(["你", "好"]);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      content: "你好",
      model: "deepseek-v4-pro"
    });
  });
});
