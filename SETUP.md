# RAY setup — the code stays in Git, you never download a zip

Everything below runs the repository **as it is on GitHub**. There is no release
archive to unpack, no installer to download, and no copy of the source to keep in
sync by hand: you run a checkout, and `git pull` is the update.

Pick one of four ways to run it:

| # | Where it runs | What RAY can control | Good for |
|---|---|---|---|
| **A** | The live preview in this chat (already running) | the sandbox container it runs in | trying commands, editing code, seeing the UI |
| **B** | GitHub Codespaces (browser only) | the Codespace container | no local install at all, full shell + UI |
| **C** | Your Windows PC (one `git clone`) | **your actual PC** — apps, files, volume, power | real daily use |
| **D** | Any Linux VM / VPS | that server | always-on automation, remote access |

> RAY is local-first: it can only touch the machine it runs on. A cloud container
> cannot open *your* Explorer window or read *your* Downloads folder. Choose C if
> you want it to drive your PC; choose A, B or D to try it or to run it headless.

---

## A. It is already running here

The preview on port 8765 is a real RAY instance: same engine, same 80 tools,
same UI. Nothing to install.

Try, in the chat box on Home:

```
help
how much ram am i using
what is using my cpu
show my processes
make a note that the deploy key rotates on friday
remind me in 20 minutes to stretch
```

Sandbox limits are honest ones: no Windows apps, no volume or brightness
hardware, and the folders are the container's, not yours.

---

## B. GitHub Codespaces — browser only, zero local download

1. Open the repo → **Code ▾ → Codespaces → Create codespace on main**
   (the included `.devcontainer` installs Python and the four dependencies).
2. In the Codespace terminal:

   ```bash
   RAY_ALLOW_REMOTE_ORIGIN=1 python3 main.py --headless --host 0.0.0.0 --port 8765 --no-open
   ```

   `--host 0.0.0.0` makes the port reachable by the forwarded URL, and
   `RAY_ALLOW_REMOTE_ORIGIN=1` tells RAY that the page is served through
   GitHub's proxy (its `Origin` differs from `Host`).
3. Open the **Ports** tab → port `8765` → globe icon. The forwarded URL is your
   RAY interface. Keep the port **private** unless you also set a token:

   ```bash
   RAY_API_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')" \
   RAY_ALLOW_REMOTE_ORIGIN=1 python3 main.py --headless --host 0.0.0.0 --port 8765 --no-open
   ```

   The UI reads the token from the page it is served, so you do not type it.

### B.1 Walkthrough, with all four providers

1. **Get the current code.** If PR #1 is merged, create the Codespace on
   **`main`**. If it is not merged yet, create it on the branch
   `arena/84eddd81-ray` (Code ▾ → Codespaces → … → *New with options* → branch).
2. **Create the codespace.** Repository → **Code ▾ → Codespaces → Create
   codespace**. The included `.devcontainer` installs Python 3.11 and the four
   dependencies; wait for "Configuration complete".
3. **Add the four keys as Codespaces secrets** (they survive rebuilds and are
   never written into the repo): GitHub avatar → **Settings → Codespaces →
   New secret**. For each one set *Repository access* to
   `revanthshankars-dotcom/RAY`.

   | Secret name | Value from | Needed for |
   |---|---|---|
   | `RAY_GEMINI_API_KEY` | <https://aistudio.google.com/apikey> | multi-step planning, documents, fallback chat — **the important one** |
   | `RAY_FISH_API_KEY` | <https://fish.audio> → API keys | spoken replies |
   | `RAY_CURSOR_API_KEY` | cursor.com dashboard → API keys | chat/reasoning without a CLI login (optional — `cursor-agent login` also works) |
   | `RAY_GROK_API_KEY` | <https://console.x.ai> | optional legacy path; skip unless you want it |
   | `RAY_API_TOKEN` | any 32+ random characters (`python3 -c "import secrets;print(secrets.token_urlsafe(32))"`) | protects the forwarded web endpoint — not a provider key |

4. **Restart the codespace** (Command Palette → *Codespaces: Rebuild Container*,
   or stop/start) so the secrets become environment variables.
5. **Optional: install the Cursor CLI** if you want Cursor as the chat engine
   without an API key:

   ```bash
   curl https://cursor.com/install -fsS | bash
   export PATH="$HOME/.local/bin:$PATH"
   cursor-agent login        # prints a URL; finish the login in your browser
   ```

6. **Start RAY** in the codespace terminal:

   ```bash
   RAY_ALLOW_REMOTE_ORIGIN=1 python3 main.py --headless --host 0.0.0.0 --port 8765 --no-open
   ```

7. **Open the interface.** *Ports* tab → `8765` → globe icon. Keep visibility
   **private**; the token is only extra protection for shared links.
8. **Confirm the providers.** In the app: **Settings → AI providers** — Gemini,
   Fish and Cursor should show *configured* (from the environment). Press
   **Test** on each. Don't paste a key in the UI if the secret already exists;
   the UI value would just duplicate it.
9. **Prove it works.** Try `"how much ram am i using"` (local, no key needed),
   then a multi-step request such as `"find my five largest files and make a
   note with their paths"` — that one needs Gemini.

Checks in the codespace terminal:

```bash
python main.py --check     # python, packages, data home, which providers are configured
python -m pytest -q        # 252 tests, offline
```

**What a Linux codespace can and cannot do.** It can plan, chat, research, read
and index files inside the workspace, manage tasks/notes/reminders/calendar and
run automation. It cannot open Windows apps, change your PC's volume or
brightness, type into other windows, use the clipboard, lock or sleep your PC,
or open Windows settings pages — those tools belong to the machine RAY runs on.
Use the live preview here or the codespace to explore, and the Windows install
(section C) when you want RAY to drive your own PC.

**Keep it cheap.** Stop the codespace when you are done (it keeps its disk and
`.runtime` data); stop, don't delete, unless you want to lose the vault and
history. `git pull` later updates the code without touching `.runtime`.

---

## C. Your Windows PC — one command, source stays in Git

Install Git once if you do not have it (`winget install Git.Git`), then in
PowerShell:

```powershell
git clone https://github.com/revanthshankars-dotcom/RAY.git "$env:LOCALAPPDATA\RAY-app"
cd "$env:LOCALAPPDATA\RAY-app"
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -WithDesktop -WithAutomation
.venv\Scripts\python.exe main.py
```

`install.ps1` creates `.venv`, installs the runtime, optionally the native window
(`-WithDesktop`), synthetic input (`-WithAutomation`) and offline speech
(`-WithSpeech`), then prints an environment check. After that, start it with
`RAY.bat` or `main.py` — the same files that are on GitHub.

**Updating** is a pull, not a reinstall. Your data lives in
`%LOCALAPPDATA%\RAY`, so it is untouched:

```powershell
cd "$env:LOCALAPPDATA\RAY-app"; git pull; .venv\Scripts\python.exe main.py
```

---

## D. Any Linux VM or VPS

```bash
git clone https://github.com/revanthshankars-dotcom/RAY.git ~/ray-app && cd ~/ray-app
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
RAY_HOME=~/ray-data RAY_API_TOKEN='pick-a-long-random-string' \
  .venv/bin/python main.py --headless --host 0.0.0.0 --port 8765 --no-open
```

Put it behind a reverse proxy with TLS, or bind `127.0.0.1` and reach it through
an SSH tunnel (`ssh -L 8765:127.0.0.1:8765 you@host`). Never expose port 8765 to
the open internet without the token.

```ini
# ~/.config/systemd/user/ray.service  (optional, keeps it running)
[Unit]
Description=RAY
[Service]
WorkingDirectory=%h/ray-app
Environment=RAY_HOME=%h/ray-data
Environment=RAY_API_TOKEN=replace-me
ExecStart=%h/ray-app/.venv/bin/python main.py --headless --host 127.0.0.1 --port 8765 --no-open
Restart=on-failure
[Install]
WantedBy=default.target
```

---

## Giving the agents their API keys

The agents themselves have no keys — **RAY's providers do**, and every agent
(files, research, productivity, computer…) borrows the same configured
providers. There are exactly two ways to supply them.

### Option 1 — in the interface (recommended)

1. Open RAY → **Settings → AI providers**.
2. For each provider: paste the key → **Save & test**. RAY encrypts it with a
   local Fernet key (`secret.key`, mode `0600`) and only ever reports
   `configured: true` — never the value.
3. **Test** per provider, and watch the status dot on Home → *AI providers*.

| Provider | What it powers | Where the key comes from | Needs |
|---|---|---|---|
| **Cursor** (primary chat/reasoning) | answers, code, reasoning | `cursor-agent login` (no key needed) or an API key from cursor.com | the `cursor-agent` CLI installed |
| **Gemini** | tool planning, long documents, PDFs, fallback chat | <https://aistudio.google.com/apikey> | free tier is enough |
| **Fish Audio** | spoken replies | <https://fish.audio> → API keys | optional |
| **Grok** (xAI, off by default) | legacy reasoning path | <https://console.x.ai> | optional |

Cursor CLI:

```powershell
irm 'https://cursor.com/install?win32=true' | iex ; cursor-agent login   # Windows
```
```bash
curl https://cursor.com/install -fsS | bash ; cursor-agent login        # Linux/macOS
```

Gemini is the one that unlocks multi-step commands, because the Cursor CLI
cannot call RAY's tools. Without it, plain-English single commands and local
questions still work; multi-step jobs answer with "add a key" instead of
pretending.

### Option 2 — environment variables (cloud, VPS, CI, Codespaces)

| Variable | Alias accepted | Provider |
|---|---|---|
| `RAY_CURSOR_API_KEY` | `CURSOR_API_KEY` | Cursor API key (optional; login also works) |
| `RAY_GEMINI_API_KEY` | `GEMINI_API_KEY`, `GOOGLE_API_KEY` | Gemini |
| `RAY_FISH_API_KEY` | `FISH_API_KEY`, `FISH_AUDIO_API_KEY` | Fish Audio |
| `RAY_GROK_API_KEY` | `XAI_API_KEY`, `GROK_API_KEY` | Grok / xAI |
| `RAY_API_TOKEN` | — | not a provider: protects the local API itself |

Rules that matter:

* A key saved in the interface **wins** over the environment.
* RAY does **not** read `.env` files — nothing to leak by accident.
* Never paste a key into chat, a note or a task. RAY refuses secrets in notes and
  memory, and refuses to read `.env`/key-store files at all.
* Rotate by **Remove** then **Save & test** in Settings, or change the variable
  and restart.
* Check everything at once: **Security → Run full check** (13 verifiable
  checks), or `python main.py --check` in the terminal.

---

## Plain-English commands (no provider required)

| You say | RAY does |
|---|---|
| "open chrome", "open my downloads", "go to youtube" | launch an app / folder / site |
| "set volume to 30", "make it louder", "take a screenshot" | hardware control (level 1–2) |
| "lock my pc", "open bluetooth settings", "close notepad" | system control (approval first) |
| "find my tax pdf", "search my files for invoice", "find big files" | local file search |
| "organize my downloads", "back up my documents" | tidy-up plan, verified backup |
| "make a folder called invoices in documents" | create a folder |
| "remind me in 20 minutes to stretch", "remind me to call mom at 7pm" | reminder with a real time |
| "add a task to buy milk", "how many tasks do I have" | tasks |
| "schedule a meeting tomorrow at 4pm", "what's on my calendar" | calendar |
| "make a note that …", "remember that I prefer dark mode" | notes and long-term memory |
| "how much ram am i using", "what's using my cpu", "how much disk space is left" | real metrics, real process list |
| "index my documents", "index status" | search index |
| "start a focus session for 25 minutes" | focus timer |
| "search the web for …", "research cheap SSDs" | web search / sourced research (needs internet) |
| "type hello world", "press ctrl+s", "read my clipboard" | input control (approval first) |
| "help", "what can you do" | the live capability list |

Anything longer ("compare three laptops and draft an email") needs a provider —
that is what Gemini/Cursor are for. RAY says so instead of guessing.

---

## First five minutes

1. Say **"help"** — you should get the capability list, not an error.
2. Say **"how much ram am i using"** — real numbers from this machine.
3. Say **"remind me in 20 minutes to stretch"** — check Reminders.
4. **Settings → AI providers** → add Gemini, press **Test**.
5. Say **"open my downloads"** (local install) or **"index status"**.
6. **Security → Run full check** — 13/13 checks, warnings only for deliberate
   network exposure.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `address already in use` | `python main.py --port 8766`, or stop the other instance |
| "I can't reach an AI provider" | no key configured: Settings → AI providers, or set `RAY_GEMINI_API_KEY` |
| Chat works, multi-step commands don't | the planner needs Gemini (Cursor CLI cannot call tools) |
| Page loads but every call returns 401 | `RAY_API_TOKEN` is set; the UI carries it automatically — scripts must send `X-RAY-Token` |
| Works locally, not through Codespaces/proxy | start with `--host 0.0.0.0` **and** `RAY_ALLOW_REMOTE_ORIGIN=1` |
| Volume/brightness "not supported on this platform" | Linux container without those APIs — expected; works on Windows |
| Windows blocks the script | `powershell -ExecutionPolicy Bypass -File scripts\install.ps1` |
| Anything else | `python main.py --check` prints Python, packages, data home and provider state |

---

## Update, back up, remove

* **Update:** `git pull` in the checkout, restart RAY. Nothing in your data
  folder is touched.
* **Back up:** Security Center → back up, or copy `%LOCALAPPDATA%\RAY`
  (`~/.local/share/ray` on Linux). The folder holds the DB, config, logs, the
  encrypted vault and its key — treat the copy as a secret.
* **Remove:** delete the checkout and the data folder. There is no service, no
  registry entry, and no uninstaller to run.
