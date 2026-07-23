# Mio Core

Mio Core 是 Music Mizu 的私人运营内核：受邀用户可以与 Mio 聊天、断点上传音乐，并由后端的确定性流水线整理 Faircamp 目录、构建网站和推送 Git。模型没有文件、路径、Shell、Git 或密钥权限。

## 架构与边界

- `mio-web`：只监听 `127.0.0.1:8787`，提供 React 前端、邀请认证、私人聊天、上传和 SSE 状态。
- `mio-worker`：串行执行隔离区验证、标签/封面读取、Faircamp 构建、路径审计和非强制 Git 推送。
- SQLite：保存用户、私聊、任务事件、模型审计和发布记录。
- [Kimi Code](https://www.kimi.com/code/docs/)：OpenAI-compatible 流式接口；API Key 只从 `MIO_LLM_API_KEY` 环境变量读取。
- Music Mizu：使用服务器专用 bare clone 和临时 worktree，不接触任何桌面 checkout。

上传文件先进入仓库外的 `data\quarantine`，经 SHA-256、文件头、FFprobe 和可选 Windows Defender 检查后才进入任务。模型仅能看到清洗后的文本元数据；音乐字节、服务器路径、Git 参数和密钥永不进入提示词。

## 本地开发

需要 Python 3.12、Node.js 22、FFmpeg/FFprobe 和 Faircamp。

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Push-Location frontend
npm install
npm run build
Pop-Location
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m mio_core.main
```

另开终端运行：

```powershell
.\.venv\Scripts\python.exe -m mio_core.worker
```

首次访问登录页，选择“初始化管理员”，填入 `.env` 中一次性的 `MIO_BOOTSTRAP_TOKEN`。创建管理员后应从 `.env` 删除该值并重启服务。

开发时可用 Vite：

```powershell
Set-Location frontend
npm run dev
```

## Kimi 配置

只在旧电脑本地的 `C:\MioCore\app\.env` 中填写：

```dotenv
MIO_LLM_API_KEY=你的密钥
MIO_LLM_BASE_URL=https://api.kimi.com/coding/v1
MIO_LLM_MODEL=kimi-for-coding
```

不要把真实 Key 发到聊天、写入前端或提交 Git。Kimi 不可用、超时、429 或断网时，聊天会显示降级提示，但已经进入队列的确定性音乐流水线仍可运行。

## Deploy Key

1. 在旧电脑生成专用密钥：`ssh-keygen -t ed25519 -f C:\MioCore\data\keys\musicmizu -C mio-core`。
2. 在 `shizwd/musicmizu` 的 Settings → Deploy keys 添加公钥，并勾选写权限。
3. 私钥只授予 Mio 服务账号读取；不要复用个人 SSH Key。
4. 在 `.env` 设置：

```dotenv
MIO_MUSIC_REMOTE=git@github.com:shizwd/musicmizu.git
MIO_GIT_SSH_COMMAND=ssh -i C:\MioCore\data\keys\musicmizu -o IdentitiesOnly=yes
```

Mio Core 永不 force push。非快进时会自动从最新 `origin/main` 重建一次；再次竞争则失败并等待管理员重试。

## Windows 服务部署

建议创建没有交互式登录权限的本地服务账号，只授予：

```text
C:\MioCore\app
C:\MioCore\data
C:\MioCore\workspaces
```

从 WinSW 官方发行版取得 `WinSW-x64.exe`，然后以管理员 PowerShell 运行：

```powershell
.\scripts\install.ps1 `
  -ServiceUser '.\mio-service' `
  -ServicePassword (Read-Host -AsSecureString) `
  -WinSWPath 'C:\Users\you\Downloads\WinSW-x64.exe'
```

安装脚本建立 `MioWeb` 与 `MioWorker` 两个自动恢复服务。升级前先运行 `scripts\backup.ps1`，再以新 checkout 调用 `scripts\upgrade.ps1 -SourceRoot <新版本路径>`；恢复使用 `scripts\restore.ps1`。备份含私人聊天和 Deploy Key，必须存放在受 ACL/磁盘加密保护的位置。

## 内网穿透

网页本身拒绝绑定公网网卡。将 `127.0.0.1:8787` 交给支持 HTTPS 的隧道或反向代理，并确保：

- 对外只开放 HTTPS；
- 保留 `Host` 和 `X-Forwarded-*`；
- SSE 禁用代理缓冲，读取超时大于 75 秒；
- 单次代理请求至少允许 9 MiB（每个分块为 8 MiB）。

`deploy/` 内提供 Nginx 与 FRP 模板。EdgeOne 继续只监听 `shizwd/musicmizu` 的 `main` 分支，与 Mio Core 服务入口互相独立。

## 数据与任务状态

- 上传：`created → uploading → quarantined → validated`，失败进入 `rejected`。
- 发布：`analyzing → awaiting_input → importing → building → committing → pushing → live|failed`。
- 每用户同时一个模型请求、全局两个模型请求、全局一个 Worker 发布任务。
- 500 MiB/文件、5 GiB/用户进行中批次。

管理员可创建/撤销一次性邀请、查看失败原因、重试任务和用正常 revert commit 回滚发布。成功发布动态对所有已登录成员可见，聊天和上传任务按用户隔离。

## 测试

```powershell
.\.venv\Scripts\python.exe -m ruff check mio_core tests
.\.venv\Scripts\python.exe -m pytest --cov=mio_core
Set-Location frontend
npm run build
```

CI 同时运行 secret scan。仓库只提交 `.env.example`，不应出现任何真实密钥。
