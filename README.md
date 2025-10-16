# AI Studio Proxy API

AI Studio Proxy API is a Python service that mirrors the Google AI Studio web experience behind an OpenAI-compatible API surface. It drives a hardened Camoufox (anti-fingerprinting Firefox build) through Playwright automation, exposes a FastAPI backend, and bundles a Web UI and GUI launcher so you can operate Google Gemini models exactly like standard OpenAI endpoints.

---

## Key Features

- **OpenAI-compatible endpoints** – drop-in `/v1/chat/completions` support for OpenAI SDKs, CLI tools, and third-party clients.
- **Three-layer streaming pipeline** – stream proxy, optional helper service, and Playwright browser scraping ensure responses even when one layer fails.
- **Model switching** – pass `model` in your request to pivot Google AI Studio models on the fly.
- **Full parameter coverage** – honours `temperature`, `max_output_tokens`, `top_p`, `stop`, `reasoning_effort`, and streaming flags.
- **Camoufox browser control** – mitigates fingerprinting detection compared to vanilla Playwright automation.
- **Script injection v3** – uses Playwright’s interception APIs for 100% reliable user-script injection.
- **Modern Web UI** – chat console, health status, model controls, log streaming, and API key vault.
- **Comprehensive GUI launcher** – start and monitor FastAPI, stream proxy, and Camoufox processes with port management helpers.
- **Optional API key auth** – mimic OpenAI Bearer tokens or define custom header schemes.
- **Unified configuration** – `.env` driven settings, shared between CLI, GUI, and Docker images.
- **Developer friendly** – Poetry for dependency management, Pyright types for IDEs, rich docs for common tasks.

---

## Supported Platforms and Requirements

| Component | Requirement |
|-----------|-------------|
| Python | 3.9 – 3.11 (3.10+ recommended; Docker images ship with 3.10) |
| OS | Windows, macOS, Linux (x86_64 and ARM64). Docker support included. |
| Dependencies | Poetry, Node.js (Playwright runtime), Camoufox distribution |
| Browser | Camoufox (custom Firefox build) |
| Memory | Minimum 2 GB free RAM for browser automation |
| Network | Stable internet path to https://aistudio.google.com (proxy variables supported) |

Optional: Pyright for static checks, `tmux`/`screen` for server background tasks.

---

## Repository Layout

```
├── api_utils/          # FastAPI application, routing, request handling
├── browser_utils/      # Camoufox + Playwright controllers and helpers
├── stream/             # Streaming proxy service
├── config/             # Config parsers and helpers
├── gui_launcher.py     # Cross-platform Tkinter GUI launcher
├── webui.js / index.html / webui.css  # Web UI frontend
├── scripts/install.ps1 / install.sh   # Guided environment installers
├── docker/             # Dockerfiles, compose, and container docs
└── docs/               # Detailed manuals (deployment, troubleshooting, etc.)
```

---

## End-to-End Setup Guide

Follow these steps to bring the proxy online and route Google Gemini traffic through the OpenAI-compatible API.

### 1. Clone and bootstrap the repository

```bash
# Clone the project
git clone https://github.com/CJackHwang/AIstudioProxyAPI.git
cd AIstudioProxyAPI
```

Install the Rust Toolchain

You need to install the Rust compiler so that Poetry can build `pydantic-core`. This is a one-time setup on your machine.

**Step 1: Download and Run the Rust Installer**

1.  Go to the official Rust installation website: **https://www.rust-lang.org/tools/install**
2.  Download the `rustup-init.exe` for 64-bit Windows.
3.  Run the installer. It will open a command prompt window.

**Step 2: Proceed with the Installation**

The installer will present you with three options. For most users, the default is perfect.

```bash
1) Proceed with installation (default)
2) Customize installation
3) Cancel installation
>
```

Simply press **`1`** and then **Enter** to start the default installation. It will download and set up the Rust compiler (`rustc`), the package manager (`cargo`), and other necessary tools.

```bash
# Install Poetry if you do not have it yet
curl -sSL https://install.python-poetry.org | python3 -

# Install runtime dependencies
poetry install
```

> Windows users can run `scripts\install.ps1`; macOS/Linux users can run `scripts/install.sh`. These helper scripts verify Python, install Poetry, fetch Camoufox, install Playwright, and create the `.env` skeleton automatically.

### 2. Install Camoufox

Camoufox is distributed separately.

1. Download the latest Camoufox release (`camoufox-win.7z`, `camoufox-mac.tar.gz`, or `camoufox-linux.tar.gz`) from [https://camoufox.com/](https://camoufox.com/).
2. Extract it to a persistent location (for example `C:\camoufox` or `/opt/camoufox`).
3. Update the following keys in your `.env` file:

```
CAMOUFOX_BROWSER_PATH=/path/to/camoufox
CAMOUFOX_PROFILE_DIR=/path/to/camoufox-profile
CAMOUFOX_WS_ENDPOINT=ws://127.0.0.1:9222
```

4. If you ran the install scripts, these paths are set for you after extraction.

### 3. Install Playwright browsers

Playwright ships with the repository as a Poetry dependency; you still need to pull the Firefox runtime.

```bash
poetry run playwright install firefox
```

The automated install scripts execute this command for you. If you maintain separate virtual environments, ensure the command runs inside the Poetry shell.

### 4. Configure environment variables

Copy the sample file and adjust ports, proxies, and helper settings:

```bash
cp .env.example .env
```

Important keys:

| Variable | Description |
|----------|-------------|
| `DEFAULT_FASTAPI_PORT` | REST API listen port (default 2048). |
| `DEFAULT_CAMOUFOX_PORT` | DevTools port Camoufox listens on (default 9222). |
| `STREAM_PORT` | Streaming proxy port (default 3120). Set `0` to disable. |
| `GOOGLE_AI_EMAIL` / `GOOGLE_AI_PASSWORD` | Optional credentials if you rely on helper services. |
| `HTTP_PROXY` / `HTTPS_PROXY` | Upstream proxies for Camoufox/Playwright. |
| `API_AUTH_MODE` | `none`, `bearer`, or `custom`. |
| `API_KEYS_DIR` | Directory that contains saved API keys for the GUI/Web UI. |

Every launcher (CLI, GUI, Docker) references this file at startup.

### 5. Obtain a Google AI Studio API key

1. Visit [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey).
2. Sign in with the Google account that has Gemini access.
3. Create a new API key (or reuse an existing one) and copy it.
4. Treat the key as sensitive; it authorises your Gemini quota.

### 6. Launch the GUI helper (recommended)

```bash
poetry run python gui_launcher.py
```

Within the GUI:

1. Confirm FastAPI and Camoufox ports.
2. Optionally enable the stream proxy and helper service toggles.
3. Click **Launch Headed Mode** to open Camoufox for interactive login. A browser window appears; sign in if prompted.
4. Once authenticated, you can close the Camoufox window and relaunch in headless mode.
5. Use the **Query Port Processes** tool if you need to inspect or free ports.

The launcher keeps all subprocess logs visible and can terminate them cleanly when you exit.

### 7. Verify the Web UI and API key

1. Serve `index.html` directly or run `poetry run uvicorn api_utils.app:app --reload` to serve both API and UI (depending on your deployment preference).
2. Open the Web UI in a browser (usually `http://localhost:2048` or the static file path).
3. Go to **API Key Management** → paste your Google AI Studio key → **Verify Key**. This persists the key in browser storage and in the server vault (if configured).
4. Check the **Server Status** view to confirm the health probes are green and the FastAPI service is reachable.

### 8. Drive Gemini CLI through the proxy

Configure your CLI or SDK to use the proxy instead of the native Google endpoint.

Example with the OpenAI CLI:

```bash
export OPENAI_API_BASE="http://127.0.0.1:2048/v1"
export OPENAI_API_KEY="dummy"   # The proxy reads the real key from storage
openai chat.completions.create -m "gemini-pro" -g "What is Project Starline?"
```

Example with `curl`:

```bash
curl http://127.0.0.1:2048/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer dummy" \
  -d '{
        "model": "gemini-pro",
        "messages": [{"role": "user", "content": "Explain differential privacy."}]
      }'
```

If you collected a key via the Web UI, the proxy injects it automatically; otherwise supply the key explicitly with the Bearer header or configure `auth_profiles/key.txt`.

---

## Operational Notes

### Streaming stack behaviour

1. **Stream proxy** (port 3120) intercepts SSE responses and relays them to clients.
2. If the proxy is disabled or fails, the **helper service** can replay cached responses (configure endpoint in `.env`).
3. As a final fallback, Playwright scrapes the Camoufox page for new content and emits it via the API.

### Authentication modes

- `none` – no API key required.
- `bearer` – expects `Authorization: Bearer <key>`; keys are compared with the stored list.
- `custom` – refer to `docs/authentication-setup.md` to define header patterns or query parameters.

### Logging

- Web UI streams logs over WebSocket `/ws/logs`.
- GUI launcher writes `logs/gui_launcher.log`.
- FastAPI logs rotate under `logs/` (configure in `.env`).
- Use `docs/logging-control.md` for advanced tuning.

---

## Docker Workflow

A Docker deployment lives under `docker/`.

```bash
cd docker
cp .env.docker .env
nano .env  # adjust paths, proxy, credentials

docker compose up -d
```

After the first run, open the Camoufox container to authenticate Google, or mount a pre-populated Camoufox profile from the host. For detailed instructions see `docker/README-Docker.md`.

---

## Documentation Index

- `docs/environment-configuration.md` – exhaustive `.env` descriptions.
- `docs/authentication-setup.md` – enabling API keys and custom auth logic.
- `docs/daily-usage.md` – operating tips once the service is running.
- `docs/streaming-modes.md` – internals of the streaming pipeline.
- `docs/script_injection_guide.md` – bringing your own userscripts (v3).
- `docs/troubleshooting.md` – fixes for the most common failure modes.

---

## Support and Contributions

Issues and pull requests are welcome. Please detail browser versions, OS, and exact logs when reporting problems; it helps reproduce automation edge cases quickly.

If this proxy saves you time, consider supporting the maintainers (see `支持作者.jpg`). The original author and community contributors invest significant effort keeping the flows stable as Google updates AI Studio.

---

## License

Released under the [AGPLv3](LICENSE).
