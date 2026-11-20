# RAY — Real-time Autonomous Yourself

A local-first personal AI operating system for Windows. RAY runs on your own
machine, keeps its data in one folder you control, and drives real Windows
features — files, apps, tasks, notes, reminders, scheduling, monitoring,
automation — through a validated command layer instead of pretending.

- **Engine**: Python 3.10+, SQLite (WAL) + FTS5, standard library HTTP server.
- **Interface**: its own window (PySide6/QtWebEngine) showing a local web UI
  served at `127.0.0.1:8765`. No build step, no external assets, no CDN. The
  Home screen is a command centre: greeting and day progress, a live RAY orb,
  CPU/RAM/disk/temperature rings with network rate and uptime, an inline
  assistant (Chat / Research / Analyse), today's schedule, quick tools, a
  tile for every section, a real security scan, automation & agents, recent
  files, a month calendar, system actions, voice, media, browser links and
  notifications. A `Think · Plan · Automate · Execute` strip in the top bar
  jumps to the matching section, and a floating dock keeps six sections one
  click away.
- **AI**: Cursor CLI for reasoning (Grok has been retired from the default
  routes), Gemini for knowledge/documents/images/tool planning, Fish Audio for
  speech. All optional and swappable — everything that does not need a model
  (files, tasks, notes, calendar, monitoring, automation) works offline.
- **Footprint**: a few threads, no local model, ~120 MB of RAM in normal use.
  Designed for a Celeron-class laptop with 8 GB of RAM and no GPU.

---

## Quick start (Windows)

```bat
git clone <this repo> RAY
cd RAY
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -WithDesktop -WithAutomation
.venv\Scripts\python.exe main.py --check
.venv\Scripts\python.exe main.py
```

Or double-click **RAY.bat** (it uses `.venv` automatically if present).

`--check` prints your Python version, which optional packages are present, the
data folder, and whether the native window is available.

### Other ways to run

| Command | What happens |
| --- | --- |
| `python main.py` | Native window on Windows; browser elsewhere |
| `python main.py --browser` | Opens the interface in your default browser |
| `python main.py --headless` | Engine + local API only (automation, reminders) |
| `python main.py --port 9000` | Different port (falls back automatically if busy) |
| `python main.py --check` | Environment report |
| `python main.py --no-open` | Start without opening any window |

Data lives in `%LOCALAPPDATA%\RAY` on Windows (`~/.local/share/ray` elsewhere),
overridable with `--home` or the `RAY_HOME` environment variable:

```
config.json      settings (never secrets)
ray.db           SQLite database (tasks, notes, index, audit, usage…)
secret.key       Fernet key protecting the credential vault (mode 0600)
quarantine/      recoverable deletes + manifest
backups/         verified archives, including a copy of the database
screenshots/     screenshots you take
logs/            rotating, redacted logs
```

---

## First run: connect your AI providers

Open **Settings → AI providers**. Keys are encrypted with a local Fernet key and
never appear in the interface, logs or `config.json`.

| Provider | Role | Setup | Default model |
| --- | --- | --- | --- |
| **Cursor** (default) | reasoning, chat, code | official CLI — `curl https://cursor.com/install -fsS \| bash`, then `cursor-agent login` | account default |
| **Gemini** (Google) | knowledge, long documents, images/PDF, tool planning | key from <https://aistudio.google.com/apikey> | `gemini-2.5-flash` |
| **Fish Audio** | text to speech | key from <https://fish.audio> | `s2.1-pro-free` |
| **Grok** (xAI, opt-in) | legacy reasoning engine | key from <https://console.x.ai> | `grok-4` |

### Cursor instead of Grok

Cursor publishes no OpenAI-compatible chat endpoint, so RAY drives the official
Cursor CLI in read-only print mode:

```
cursor-agent --print --mode ask --output-format text "<prompt>"
```

* Windows: `irm 'https://cursor.com/install?win32=true' | iex`, then
  `cursor-agent login`.
* RAY never passes `--force`/`--yolo`, so a reply cannot edit files or run
  commands on your PC.
* The CLI cannot call RAY's own tools, so **planning and tool selection keep
  using Gemini**; chat, reasoning and code go to Cursor first.
* An optional Cursor API key can be stored in Settings instead of logging in
  (passed to the CLI as `CURSOR_API_KEY`).
* If the CLI is missing, RAY says so in **Home → AI providers → Set up** and
  falls back to Gemini for chat.

Fish Audio: paste a **voice model id** (`reference_id`) if you want one of your
cloned voices; leave it empty for the default voice. Click **Load voices** to
list the voices on your account, and **Test** to verify each provider.

You can also supply keys through the environment — `RAY_GEMINI_API_KEY`,
`RAY_FISH_API_KEY` and, if you keep Grok, `RAY_GROK_API_KEY` (aliases
`GEMINI_API_KEY`, `FISH_AUDIO_API_KEY`, `XAI_API_KEY`, `CURSOR_API_KEY` are
recognised). A key saved in the interface wins over the environment. RAY does
**not** read `.env` files.

Without keys, RAY runs in offline mode: local commands, files, productivity,
monitoring and automation all work; chat and speech say plainly that a provider
is not configured.

---

## What each section does

| Section | Real behaviour |
| --- | --- |
| **Home** | Greeting, date, live CPU/RAM/disk, today's schedule, tasks, reminders, recent files/apps, automation status, security state |
| **Assistant** | Chat with provider routing; memory context; plans that run tools and show each step |
| **Voice** | Microphone selection, push-to-talk or wake word (browser speech recognition), Fish Audio playback, visible mic state, stop button |
| **Computer** | Launch/close apps, volume, brightness, screenshots, clipboard, keyboard/mouse (approval-gated), power actions with confirmation |
| **Files** | Browse, search (indexed or live), open, reveal, create folder, rename, copy, move, plan/apply Downloads tidy-up, verified zip backups, duplicate and large-file reports |
| **Browser** | Web search, read a page, research with citations saved as a report — clearly separates fetched text from AI commentary |
| **Productivity** | Focus sessions (pomodoro), habits and streaks, today's workload |
| **Calendar** | Month/week/day views backed by the local database |
| **Notes** | Create, search, pin, autosave, Markdown export |
| **Tasks** | Priorities, due dates, recurrence, categories, completion stats |
| **Reminders** | Natural-language times, recurrence, snooze, desktop notifications |
| **Automation** | No-code workflows: manual / interval / daily / file-added / on-start triggers, conditions, steps, history, per-workflow enable switch |
| **Agents** | The 12 agents, their remit, their allowed tools and recent runs |
| **Knowledge** | Watch folders, index PDF/DOCX/XLSX/PPTX/TXT/MD/CSV, full-text search; **nothing is uploaded** unless you explicitly ask a document question with cloud documents enabled |
| **Media** | Scans Music/Videos/Downloads, reads playlists, plays locally, shows audio tags when `mutagen` is installed |
| **System** | Real metrics with live charts, top processes, full system report |
| **Security** | Three permission levels, pending approvals, audit log, protected paths, emergency stop, session lock, backups |
| **Analytics** | AI usage and token counts per provider, cache hit rate, focus minutes, database size, error history |
| **Downloads** | Watches the Downloads folder, opens files, shows recent additions |
| **Developer** | Projects, git status, run-safe commands, code-oriented agent notes, tool catalogue |
| **Settings** | Providers, models, appearance, permissions, automation, indexing, voice, data location, backups |

## Command examples

Type into the command center (**Ctrl+Space** or the top search box):

```
check my laptop performance
add task Finish the Companies Act notes
remind me to call the registrar at 6pm
note: section 180 covers borrowing limits
schedule a study block tomorrow 9:30 for two hours
find pdfs in downloads
open my documents folder
open chrome and search for python decorators
back up my projects folder
organise my downloads
take a screenshot
volume up
set brightness to 40 percent
lock the computer
delete the file C:\Users\me\Desktop\old.txt      ← always asks for approval
```

Free-form requests that are not one of the local patterns go to the planner
(Grok), which must choose from the registered tools and is capped by step, time
and budget limits. Approval-gated steps pause and show **Approve / Deny**.

## Keyboard

| Shortcut | Action |
| --- | --- |
| `Ctrl+Space` | Command center (also bound in the desktop window) |
| `Ctrl+K` | Global search palette |
| `Esc` | Close palette / modal |
| `Ctrl+R` | Reload the interface (desktop window) |

---

## Safety model

- **Three permission levels**: automatic (reads), confirmation (data changes),
  explicit approval (deletes, power, synthetic input, shell).
- **Approvals** are database rows with the exact tool and arguments; nothing
  destructive happens without a decision. Emergency stop blocks every tool
  except reads.
- **Deletes are recoverable**: files move to `quarantine/` with a manifest and
  can be restored from Security Center. Deleting protected roots is refused.
- **Shell commands** are off by default, accept argument lists only (never
  strings), require an allow-listed binary, reject shell metacharacters, and
  refuse inline interpreter code (`python -c`, `node -e`) unless you opt in.
- **File reads are scoped**: `.ssh`, `.aws`, `.kube`, browser profiles, `.env`,
  private keys and RAY's own vault/database are refused, even to a level-1 tool,
  so a prompt-injected "read my key file" cannot reach a model.
- **Outbound fetching is public-web only**: loopback, LAN, link-local and cloud
  metadata addresses are blocked, redirects are re-validated hop by hop, and
  bodies are capped — the fetch path cannot be used as an SSRF proxy.
- **Parsers are bounded**: PDFs, OOXML members, archives and XML are size-capped
  and `DOCTYPE`/`ENTITY` declarations are refused; RAY never extracts archives.
- **Automation** never runs destructive tools — a workflow that tries is marked
  failed rather than escalated silently.
- **No hidden uploads**: document text goes to a provider only when you ask a
  document question *and* cloud documents are enabled in Settings.
- **Credentials** are encrypted at rest, redacted from logs, never returned by
  the API and refused by memory/settings/notes if you paste one by mistake.
- **Local API hardening**: JSON-only writes, cross-site writes refused (even when
  the interface is network-bound), strict CSP, no wildcard CORS, 2 MiB body cap,
  generic 500s (details stay in the local log), rate limiting, and an optional
  bearer token (`RAY_API_TOKEN`) for shared machines. Binding beyond `127.0.0.1`
  is possible but the UI warns you that the network can reach it.
- **Data at rest is owner-only**: RAY's folders are `0700`, the database,
  config and vault key are `0600` on POSIX; backups skip credential files.
- **Clauses and audit**: [`SECURITY.md`](SECURITY.md) states one rule per area
  (API, auth, files, shell, fetch, AI, memory, automation, audit, media, data,
  install, UI); [`SECURITY_AUDIT.md`](SECURITY_AUDIT.md) records the findings,
  the fixes and the accepted risks.
- **Bounded work**: retries, timeouts, per-provider daily budgets, step limits,
  task deadlines, bounded event buffer and capped response cache.

## Project layout

```
main.py                  launcher, environment check, desktop/browser/headless modes
RAY.bat                  Windows double-click launcher
SECURITY.md              one security clause per area (the normative rule set)
SECURITY_AUDIT.md        leakage review, findings, breach scenarios, accepted risks
ray/
  core/        paths, redacting logs, config, SQLite schema/migrations, event bus
  security/    credential vault, permissions/approvals, audit, secret detection
  ai/          base types, cursor (CLI), gemini, grok (opt-in), fish, cache/usage, router
  tools/       80-tool registry + validated execution
  computer/    system metrics, apps, files (quarantine/backup), controls, shell
  productivity/ tasks, notes, reminders, calendar, focus & habits
  memory/      long-term memory with user control
  knowledge/   extraction (PDF/DOCX/XLSX/PPTX/text) and FTS5 indexer
  browser/     search, page reading, sourced research reports
  automation/  workflow engine and scheduler
  media/       library scanning and playback
  brain/       intent parser, planner, orchestrator
  agents/      the 12 agent specifications
  server/      local HTTP API + static file server
  ui/          HTML/CSS/JS interface (no build step)
  desktop/     optional PySide6 window with tray icon
scripts/       install.ps1, ui_smoke.mjs
tests/         pytest suite (212 tests)
```

## Testing

```bash
pip install pytest
python -m pytest                     # 212 tests, offline, no keys needed
```

The suite covers the database, credential vault, the hardening regressions
(SSRF, sensitive reads, parser limits, file modes, tokens, rate limits),
the permission/approval flow,
files (quarantine round-trip, backups, protected paths), productivity,
automation triggers/conditions/safety, intent parsing, orchestration, provider
request/response handling with injected HTTP responses, knowledge extraction,
memory, and the HTTP API including its security guards.

UI smoke test (optional, needs Node):

```bash
npm install jsdom
python main.py --headless &
RAY_URL=http://127.0.0.1:8765/ node scripts/ui_smoke.mjs
```

It boots the real interface in jsdom, visits all 21 sections, creates a task
through the UI and runs a command, failing on any error.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `PySide6 with QtWebEngine is not installed` | `pip install PySide6`; RAY falls back to the browser |
| `Qt could not load its system libraries: libGL.so.1` (Linux) | Install GL/X11 libs, or run `--browser` |
| Port already in use | RAY picks the next free port automatically; or pass `--port` |
| Voice says "not configured" | Add a Fish Audio key in Settings → AI providers |
| Microphone does nothing | Allow mic access for `localhost` in the browser; select the device in Voice |
| "Volume control is unavailable" | Install `pycaw` on Windows (publisher: `pip install pycaw`) |
| Keyboard/mouse tools refuse | `pip install pynput` and approve the action |
| Offline chat message | Install the Cursor CLI or add a Gemini key; local tools keep working |
| Running behind a reverse proxy | Set `RAY_ALLOW_REMOTE_ORIGIN=1`; cross-site writes are refused otherwise |

Detailed status, including what is and is not verified, is in
[FINAL_REPORT.md](FINAL_REPORT.md).

## Privacy

RAY sends data to a provider only when you ask it to (chat, research, document
questions, speech). Search, file indexing, tasks, notes, reminders, monitoring,
automation and memory stay on your machine in a SQLite file. There is no
telemetry, no analytics service, no cloud sync. Back up the data folder and you
have backed up RAY.
