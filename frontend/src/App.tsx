import { FormEvent, useEffect, useRef, useState } from "react";
import { api, Job, sendChat, setCsrf, uploadAudio, User } from "./api";

type Conversation = { id: string; title: string };
type Message = { id?: string; role: "user" | "assistant"; content: string };
type Activity = {
  id: string;
  title: string;
  publishedBy: string;
  publishedAt: string;
  commitSha: string;
  reverted: boolean;
};

const stateText: Record<string, string> = {
  analyzing: "Mio 正在分析",
  awaiting_input: "等待补充信息",
  importing: "正在整理目录",
  building: "正在构建站点",
  committing: "正在生成提交",
  pushing: "正在推送",
  live: "已上线",
  failed: "发布失败"
};

function Auth({ onReady }: { onReady: (user: User) => void }) {
  const inviteToken = location.pathname.startsWith("/invite/")
    ? decodeURIComponent(location.pathname.slice("/invite/".length))
    : "";
  const [mode, setMode] = useState<"login" | "invite" | "bootstrap">(
    inviteToken ? "invite" : "login"
  );
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const body = {
      username: String(data.get("username") || ""),
      password: String(data.get("password") || ""),
      ...(mode === "invite" ? { token: inviteToken } : {}),
      ...(mode === "bootstrap" ? { token: String(data.get("token") || "") } : {})
    };
    try {
      const user = await api<User>(
        mode === "login"
          ? "/api/auth/login"
          : mode === "invite"
            ? "/api/auth/invites/accept"
            : "/api/auth/bootstrap",
        { method: "POST", body: JSON.stringify(body) }
      );
      history.replaceState({}, "", "/");
      setCsrf(user.csrfToken);
      onReady(user);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card">
        <img src="/mio-avatar.png" alt="" />
        <p className="eyebrow">MUSIC MIZU OPERATIONS</p>
        <h1>{mode === "login" ? "欢迎回来" : mode === "invite" ? "接受 Mio 的邀请" : "初始化管理员"}</h1>
        <p className="muted">登录后才能与 Mio 对话、上传音乐和查看发布状态。</p>
        <form onSubmit={submit}>
          {mode === "bootstrap" && <input name="token" type="password" placeholder="初始化令牌" required />}
          <input name="username" autoComplete="username" placeholder="用户名" required />
          <input name="password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} placeholder="密码（至少 10 位）" required />
          {error && <p className="error">{error}</p>}
          <button className="primary" type="submit">继续</button>
        </form>
        {!inviteToken && (
          <button className="text-button" onClick={() => setMode(mode === "bootstrap" ? "login" : "bootstrap")}>
            {mode === "bootstrap" ? "返回登录" : "首次运行？初始化管理员"}
          </button>
        )}
      </section>
    </main>
  );
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    api<User>("/api/auth/me")
      .then((value) => {
        setCsrf(value.csrfToken);
        setUser(value);
      })
      .finally(() => setLoading(false));
  }, []);
  if (loading) return <div className="splash">Mio 正在醒来…</div>;
  if (!user) return <Auth onReady={setUser} />;
  return <Workspace user={user} onLogout={() => setUser(null)} />;
}

function Workspace({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [tab, setTab] = useState<"chat" | "activity" | "admin">("chat");
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [adminJobs, setAdminJobs] = useState<Job[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api<Conversation[]>("/api/conversations").then(async (items) => {
      const current = items[0] || (await api<Conversation>("/api/conversations", { method: "POST" }));
      setConversation(current);
      const detail = await api<{ messages: Message[] }>(`/api/conversations/${current.id}`);
      setMessages(detail.messages);
    });
  }, []);

  useEffect(() => {
    if (tab === "activity") api<Activity[]>("/api/activity").then(setActivity);
    if (tab === "admin" && user.role === "admin") api<Job[]>("/api/admin/jobs").then(setAdminJobs);
  }, [tab, user.role]);

  async function submitMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!conversation || busy) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const content = String(data.get("message") || "").trim();
    if (!content) return;
    form.reset();
    setMessages((current) => [...current, { role: "user", content }, { role: "assistant", content: "" }]);
    setBusy(true);
    try {
      await sendChat(conversation.id, content, (delta) => {
        setMessages((current) => {
          const next = [...current];
          next[next.length - 1] = { role: "assistant", content: next[next.length - 1].content + delta };
          return next;
        });
      });
    } catch (reason) {
      setNotice((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleFiles(files: FileList | null) {
    if (!files?.length) return;
    for (const file of Array.from(files)) {
      setNotice(`正在校验并上传 ${file.name}…`);
      try {
        const job = await uploadAudio(file, (progress) => setNotice(`${file.name} 已上传 ${progress}%`));
        setJobs((current) => [job, ...current]);
        watchJob(job.id);
        setNotice(`${file.name} 已进入 Mio 的整理队列`);
      } catch (reason) {
        setNotice((reason as Error).message);
      }
    }
  }

  function watchJob(id: string) {
    const source = new EventSource(`/api/jobs/${id}/events`);
    source.onmessage = async () => {
      const current = await api<Job>(`/api/jobs/${id}`);
      setJobs((items) => items.map((item) => (item.id === id ? current : item)));
      if (["live", "failed"].includes(current.state)) source.close();
    };
    source.onerror = () => source.close();
  }

  async function logout() {
    await api("/api/auth/logout", { method: "POST" });
    onLogout();
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><img src="/mio-avatar.png" alt="" /><span><strong>Mio Core</strong><small>潮汐档案室</small></span></div>
        <nav>
          <button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>◉ 与 Mio 对话</button>
          <button className={tab === "activity" ? "active" : ""} onClick={() => setTab("activity")}>⌁ 发布动态</button>
          {user.role === "admin" && <button className={tab === "admin" ? "active" : ""} onClick={() => setTab("admin")}>◇ 管理面板</button>}
        </nav>
        <div className="profile"><span><strong>{user.username}</strong><small>{user.role}</small></span><button onClick={logout}>退出</button></div>
      </aside>
      <main className="workspace">
        {tab === "chat" && (
          <>
            <header><div><p className="eyebrow">PRIVATE CONVERSATION</p><h1>与 Mio 整理音乐</h1></div><button className="secondary" onClick={() => fileInput.current?.click()}>＋ 上传音乐</button></header>
            <input ref={fileInput} hidden type="file" accept=".flac,.mp3,.m4a,.ogg,.opus,.wav,audio/*" multiple onChange={(event) => handleFiles(event.target.files)} />
            <section className="chat-layout">
              <div className="messages">
                {!messages.length && <MioWelcome onUpload={() => fileInput.current?.click()} />}
                {messages.map((message, index) => <article key={message.id || index} className={`message ${message.role}`}><span>{message.role === "assistant" ? "Mio" : user.username}</span><p>{message.content || "…"}</p></article>)}
              </div>
              {!!jobs.length && <aside className="jobs"><h2>发布队列</h2>{jobs.map((job) => <JobCard key={job.id} job={job} onChanged={(next) => setJobs((items) => items.map((item) => item.id === next.id ? next : item))} />)}</aside>}
            </section>
            {notice && <div className="notice" role="status">{notice}<button onClick={() => setNotice("")}>×</button></div>}
            <form className="composer" onSubmit={submitMessage}><textarea name="message" rows={1} placeholder="问 Mio，或告诉她你想怎样整理音乐…" /><button className="primary" disabled={busy}>发送</button></form>
          </>
        )}
        {tab === "activity" && <ActivityView items={activity} />}
        {tab === "admin" && <AdminView jobs={adminJobs} onRefresh={() => api<Job[]>("/api/admin/jobs").then(setAdminJobs)} />}
      </main>
      <img className="standee" src="/mio-standee.png" alt="" />
    </div>
  );
}

function MioWelcome({ onUpload }: { onUpload: () => void }) {
  return <section className="welcome"><p className="eyebrow">MIO IS READY</p><h2>把音乐交给我吧。</h2><p>我会检查标签和封面；信息不全时先问清楚，再通过受控流水线发布到 Music Mizu。</p><button className="secondary" onClick={onUpload}>选择音乐文件</button></section>;
}

function JobCard({ job, onChanged }: { job: Job; onChanged: (job: Job) => void }) {
  const [saving, setSaving] = useState(false);
  async function answer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    const data = new FormData(event.currentTarget);
    const payload: Record<string, string | number> = {};
    for (const field of ["title", "artist", "album"]) if (data.get(field)) payload[field] = String(data.get(field));
    if (data.get("track_number")) payload.track_number = Number(data.get("track_number"));
    const next = await api<Job>(`/api/jobs/${job.id}/answers`, { method: "POST", body: JSON.stringify(payload) });
    if (job.requiredFields.includes("cover")) {
      const cover = data.get("cover");
      if (cover instanceof File && cover.size) {
        const form = new FormData();
        form.append("file", cover);
        onChanged(await api<Job>(`/api/jobs/${job.id}/cover`, { method: "POST", body: form }));
      }
    } else onChanged(next);
    setSaving(false);
  }
  return <article className={`job-card ${job.state}`}><div><span className="status-dot" /><strong>{stateText[job.state] || job.state}</strong></div><small>{String(job.metadata.title || job.id.slice(0, 8))}</small>{job.state === "awaiting_input" && <form onSubmit={answer}>{job.requiredFields.map((field) => field === "cover" ? <input key={field} name="cover" type="file" accept="image/jpeg,image/png" required /> : <input key={field} name={field} type={field === "track_number" ? "number" : "text"} placeholder={{title:"曲名",artist:"作者",album:"专辑",track_number:"曲序"}[field] || field} required />)}<button className="primary" disabled={saving}>补充并继续</button></form>}{job.commitSha && <code>{job.commitSha.slice(0, 9)}</code>}</article>;
}

function ActivityView({ items }: { items: Activity[] }) {
  return <section className="page"><p className="eyebrow">SHARED ACTIVITY</p><h1>发布动态</h1><p className="muted">只显示成功发布的音乐；私人对话不会出现在这里。</p><div className="activity-list">{items.map((item) => <article key={item.id}><div className="album-symbol">♫</div><div><strong>{item.title}</strong><p>{item.publishedBy} · {new Date(item.publishedAt).toLocaleString()}</p></div><code>{item.reverted ? "已回滚" : item.commitSha.slice(0, 9)}</code></article>)}</div></section>;
}

function AdminView({ jobs, onRefresh }: { jobs: Job[]; onRefresh: () => void }) {
  const [invite, setInvite] = useState<{ id: string; url: string } | null>(null);
  const [logs, setLogs] = useState<Record<string, Array<{ id: number; state: string; message: string; createdAt: string }>>>({});
  const [publications, setPublications] = useState<Activity[]>([]);
  useEffect(() => {
    api<Activity[]>("/api/activity").then(setPublications);
  }, []);
  async function createInvite() {
    const result = await api<{ id: string; url: string }>("/api/admin/invites", {
      method: "POST",
      body: JSON.stringify({ role: "member", expires_hours: 72 })
    });
    setInvite(result);
  }
  async function revokeInvite() {
    if (!invite) return;
    await api(`/api/admin/invites/${invite.id}`, { method: "DELETE" });
    setInvite(null);
  }
  async function retry(id: string) {
    await api(`/api/admin/jobs/${id}/retry`, { method: "POST" });
    onRefresh();
  }
  async function toggleLogs(id: string) {
    if (logs[id]) {
      setLogs((current) => {
        const next = { ...current };
        delete next[id];
        return next;
      });
      return;
    }
    const events = await api<Array<{ id: number; state: string; message: string; createdAt: string }>>(`/api/admin/jobs/${id}/events`);
    setLogs((current) => ({ ...current, [id]: events }));
  }
  async function revert(publication: Activity) {
    await api(`/api/admin/publications/${publication.id}/revert`, { method: "POST" });
    setPublications(await api<Activity[]>("/api/activity"));
  }
  return (
    <section className="page admin-page">
      <header>
        <div><p className="eyebrow">ADMINISTRATION</p><h1>运行与发布</h1></div>
        <button className="secondary" onClick={createInvite}>生成邀请</button>
      </header>
      {invite && (
        <div className="invite-box">
          <input readOnly value={invite.url} />
          <button onClick={() => navigator.clipboard.writeText(invite.url)}>复制</button>
          <button onClick={revokeInvite}>撤销</button>
        </div>
      )}
      <h2 className="admin-section-title">任务与日志</h2>
      <div className="table">
        <div className="table-head"><span>任务</span><span>状态</span><span>提交</span><span>操作</span></div>
        {jobs.map((job) => (
          <div className="table-entry" key={job.id}>
            <div className="table-row">
              <span><strong>{String(job.metadata.title || job.id.slice(0, 8))}</strong><small>{job.lastError}</small></span>
              <span>{stateText[job.state] || job.state}</span>
              <code>{job.commitSha?.slice(0, 9) || "—"}</code>
              <span className="row-actions">
                <button onClick={() => toggleLogs(job.id)}>{logs[job.id] ? "收起" : "日志"}</button>
                {job.state === "failed" && <button onClick={() => retry(job.id)}>重试</button>}
              </span>
            </div>
            {logs[job.id] && (
              <ol className="job-log">
                {logs[job.id].map((event) => (
                  <li key={event.id}><time>{new Date(event.createdAt).toLocaleString()}</time><span>{event.message}</span></li>
                ))}
              </ol>
            )}
          </div>
        ))}
      </div>
      <h2 className="admin-section-title">发布与回滚</h2>
      <div className="activity-list">
        {publications.map((publication) => (
          <article key={publication.id}>
            <div className="album-symbol">♫</div>
            <div><strong>{publication.title}</strong><p>{publication.publishedBy} · {publication.commitSha.slice(0, 9)}</p></div>
            {publication.reverted ? <code>已回滚</code> : <button onClick={() => revert(publication)}>回滚</button>}
          </article>
        ))}
      </div>
    </section>
  );
}

export default App;
