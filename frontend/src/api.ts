export type User = {
  id: string;
  username: string;
  role: "member" | "admin";
  csrfToken: string;
};

export type Job = {
  id: string;
  uploadId: string;
  state: string;
  metadata: Record<string, unknown>;
  requiredFields: string[];
  commitSha?: string;
  lastError?: string;
};

export type ChatModel = "deepseek-v4-flash" | "deepseek-v4-pro";

let csrfToken = "";
export function setCsrf(value: string) {
  csrfToken = value;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method || "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
  return payload as T;
}

export async function sendChat(
  conversationId: string,
  content: string,
  model: ChatModel,
  onDelta: (text: string) => void
) {
  const response = await fetch(`/api/conversations/${conversationId}/messages`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    body: JSON.stringify({ content, model })
  });
  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "无法发送消息");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const event of events) {
      const line = event.split("\n").find((item) => item.startsWith("data: "));
      if (!line) continue;
      const data = JSON.parse(line.slice(6));
      if (data.type === "delta") onDelta(data.text);
      if (data.type === "error") throw new Error(data.message);
    }
  }
}

export async function sha256(blob: Blob): Promise<string | null> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return null;
  try {
    const digest = await subtle.digest("SHA-256", await blob.arrayBuffer());
    return [...new Uint8Array(digest)]
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
  } catch {
    return null;
  }
}

export async function uploadAudio(
  file: File,
  onProgress: (progress: number) => void
): Promise<Job> {
  const chunkSize = 8 * 1024 * 1024;
  const totalChunks = Math.ceil(file.size / chunkSize);
  const resumeKey = `mio-upload:${file.name}:${file.size}:${file.lastModified}`;
  let upload: { id: string; receivedChunks: number; state?: string } | null = null;
  const savedId = localStorage.getItem(resumeKey);
  if (savedId) {
    upload = await api<{ id: string; receivedChunks: number; state: string }>(
      `/api/uploads/${savedId}`
    ).catch(() => null);
    if (upload && !["created", "uploading"].includes(upload.state || "")) upload = null;
  }
  if (!upload) {
    upload = await api<{ id: string; receivedChunks: number }>("/api/uploads", {
      method: "POST",
      body: JSON.stringify({
        filename: file.name,
        size: file.size,
        total_chunks: totalChunks
      })
    });
    localStorage.setItem(resumeKey, upload.id);
  }
  for (let index = upload.receivedChunks; index < totalChunks; index += 1) {
    const chunk = file.slice(index * chunkSize, Math.min((index + 1) * chunkSize, file.size));
    const form = new FormData();
    form.append("file", chunk, `${index}.part`);
    const chunkDigest = await sha256(chunk);
    await api(`/api/uploads/${upload.id}/chunks/${index}`, {
      method: "PUT",
      headers: chunkDigest ? { "X-Chunk-SHA256": chunkDigest } : undefined,
      body: form
    });
    onProgress(Math.round(((index + 1) / totalChunks) * 100));
  }
  const result = await api<{ job: Job }>(`/api/uploads/${upload.id}/finalize`, {
    method: "POST"
  });
  localStorage.removeItem(resumeKey);
  return result.job;
}
