# RAY — final report

**Version 0.1.0 · branch `arena/84eddd81-ray` · 20 November 2026**

RAY (Real-time Autonomous Yourself) is a local-first personal AI operating
system for Windows: one Python engine, one SQLite store, a local HTTP API and an
interface that behaves like a desktop app. This report states plainly what was
built, what was actually exercised, what could not be exercised in the build
environment, and what is deliberately not implemented.

Everything below can be re-checked with the commands in **Verification**.

---

## 1. What was delivered

| Area | State | Evidence |
| --- | --- | --- |
| Engine wiring (`ray/app.py`), single data home, SQLite schema v3 + FTS5 | **Complete, tested** | `tests/test_core.py` |
| Encrypted credential vault (Fernet, mode 0600, env fallback, rotation) | **Complete, tested** | `tests/test_security.py` |
| Permission levels, approval queue, emergency stop, audit log | **Complete, tested** | 14 security tests + API approval round-trip |
| 80-tool validated command layer | **Complete, tested** | tool catalogue test, approval tests, capability probes |
| Cursor CLI + Gemini + Grok (opt-in) + Fish Audio providers, routing, retries, budgets, cache, usage | **Complete; request/response tested with injected HTTP and a stub CLI** | `tests/test_ai.py` |
| Intent parser + planner + orchestrator (progress, cancel, resume, step limits, conversational fallback) | **Complete, tested** | `tests/test_brain.py` (33) |
| 12 agents with tool remits, run log | **Complete, tested** | agent selection + `agent_runs` assertions |
| Files: browse/search/open/rename/copy/move, quarantine+restore, verified zip backups, duplicates, organiser | **Complete, tested** | `tests/test_computer.py` |
| Productivity: tasks, notes, reminders, calendar, focus sessions, habits | **Complete, tested** | `tests/test_productivity.py` (14) |
| Automation: 5 trigger types, conditions, 4 step types, history, safety refusals, scheduler | **Complete, tested** | `tests/test_automation.py` (15) |
| Knowledge: extraction (PDF/DOCX/XLSX/PPTX/TXT/MD/CSV), FTS5 index, watch folders, local summaries | **Complete, tested** | `tests/test_memory_knowledge.py` (19) |
| Long-term memory with user control (list/forget/export/import/prune, secret refusal) | **Complete, tested** | memory tests |
| Local HTTP API (~60 routes) | **Complete, tested** | `tests/test_api.py` (33) |
| Interface: 21 sections, command center, chat, voice, palette, modals, toasts, live charts | **Complete; rendered headlessly in every section** | `scripts/ui_smoke.mjs` output below |
| Desktop window (PySide6 + QtWebEngine, tray, Ctrl+Space, menu) | **Written; not runnable here** | no `libGL.so.1` in this sandbox |
| System monitor with real metrics | **Complete, tested** | CPU/memory/disk/process assertions |
| Media centre, browser/research, downloads | **Implemented; partially tested** | library scan test; research needs network |

Counts: 57 Python modules (~13.2k lines), 3 UI files (~4.3k lines), 197 tests,
80 tools, 12 agents, 21 navigation sections.

---

## 2. Verification performed

```
$ python -m pytest
197 passed in 22.0s
```

| Test file | Tests | Focus |
| --- | --- | --- |
| `test_api.py` | 34 | every major endpoint, approval resume, CSRF/content-type guards, static files, path traversal, log redaction |
| `test_brain.py` | 33 | intent patterns, time parsing, plans, step caps, approvals, cancel, resume |
| `test_ai.py` | 39 | Cursor CLI parsing/timeout/auth, Grok/Gemini/Fish parsing, tool calls, error mapping, routing fallback, budget, cache |
| `test_memory_knowledge.py` | 19 | memory CRUD/export/prune/secrets, OOXML extraction, FTS search, excludes |
| `test_computer.py` | 18 | files (quarantine round-trip, backups, protected paths), metrics, shell safety |
| `test_automation.py` | 15 | triggers, conditions, destructive refusal, history, scheduler |
| `test_productivity.py` | 14 | tasks/recurrence, notes, reminders, calendar, focus/habits |
| `test_security.py` | 14 | vault encryption, permissions, approvals, emergency stop, audit |
| `test_core.py` | 9 | config merge/validation/corruption, migrations, FTS, backup/integrity |

Interface smoke test (jsdom against the live server) — every section rendered,
a task was created through the UI, settings credential fields were present, and
the command palette executed a real system report:

```
ok home · assistant · voice · computer · files · browser · productivity ·
   calendar · notes · tasks · reminders · automation · agents · knowledge ·
   media · system · security · analytics · downloads · developer · settings
ok task creation via UI (1 task)
ok settings credential inputs
ok command palette executes real system report
UI smoke test passed.
```

Bugs found and fixed by this testing (all were real, user-visible defects):

1. `EventBus.publish(kind=…)` collided with payload key `kind` → emergency
   stop/approval events crashed. Renamed the parameter to `topic`.
2. Orchestrator returned tasks **without** `_finish()` → no `agent_runs` history,
   no `task_finished` event. Fixed.
3. A task whose steps were all outside the agent's remit reported `completed`
   having done nothing. Now fails with an explanation.
4. Local "add task …" sent `due_at: null`, which the strict validator rejected —
   the command centre could not create tasks. Validator now treats null optional
   args as absent; the planner strips them.
5. DOCX/XLSX extraction returned empty text for files with undeclared XML
   prefixes. Added a regex fallback and namespace-free cell traversal.
6. "find my meeting notes" was parsed as *create note* (bare word match). Note
   intent now requires a verb.
7. "find pdfs in downloads" lost the type filter. Type words now map to
   extension/kind filters.
8. No way to delete a file from the command centre. Added an approval-gated
   delete intent.
9. Local API accepted writes from any origin and any content type. Now JSON-only,
   loopback-origin enforced, strict CSP, no wildcard CORS.
10. Cache TTL was clamped to ≥1 s (fine) but expired rows were never purged on
    read; verified and covered.
11. Settings could store an API key in plaintext `config.json`. Now refused with
    a pointer to the vault.

---

## 3. Built but **not verified in this environment**

This sandbox is Linux (Debian 12, Python 3.11, 2 vCPU, 3.8 GB) with no display.
These parts are written, compile, and fail with explicit messages where a
capability is absent — but their real-world behaviour must be confirmed on your
Windows machine:

| Item | Why unverified | How to verify on Windows |
| --- | --- | --- |
| Native window, tray icon, menu, Ctrl+Space binding | PySide6 installed but Qt cannot load `libGL.so.1` here | `python main.py --desktop` |
| Volume control | `pycaw` not installed; Linux fallback needs `pactl/amixer` | Settings → Computer, or "volume up" |
| Screen brightness | no backlight device in the sandbox | "set brightness to 40 percent" |
| Screenshots | no `grim/scrot/imagemagick` here | "take a screenshot" |
| Clipboard read/write | no `pyperclip` and no X display | Computer → clipboard |
| Keyboard/mouse automation | no `pynput` | approve a `press_hotkey` call |
| App launching/closing | sandbox has no Windows app model | "open notepad" |
| Power actions (lock/sleep/shutdown) | intentionally guarded; not executed here | approve a `power_off` call |
| Real Cursor/Gemini/Fish calls | the Cursor CLI is not installed here, there are no API keys, and provider hosts are unreachable from this sandbox | Settings → Test each provider on your PC |
| Fish Audio sounds like your voice | needs your key/voice id | Voice → Load voices → Speak |
| Research over the live web | outbound internet restricted here | Browser → Research |
| Offline speech-to-text | `faster-whisper` not installed (large download) | Install it, or use browser speech recognition |
| Installer scripts (`install.ps1`, `RAY.bat`) | Windows-only | run them on your machine |

If a capability is missing, RAY says exactly what to install or which setting to
change — it never reports success it did not achieve.

---

## 4. Partial / limited by design

- **Knowledge search is keyword (FTS5), not semantic.** No embeddings, no local
  LLM — deliberate for Celeron/8 GB. Exact words and file names match well;
  paraphrases may not.
- **Browser research** fetches the search results page and the top pages, then
  (with a key) asks Gemini to summarise with citations. Fetched text is labelled
  as untrusted content and kept separate from AI commentary. Without a key you
  get the sources and page text, not a written analysis.
- **Media** plays files through the OS handler and reads tags only when
  `mutagen` is installed; there is no transcoding or streaming service.
- **Automation actions**: notify, speak, log, and (non-destructive) tool calls.
  Destructive tools are refused rather than queued.
- **Voice wake word** relies on the browser's speech recognition while the Voice
  page is open; it is not an always-on background listener.
- **Backups** are verified zips of chosen paths plus a database copy. There is no
  scheduled cloud backup (cloud backup is opt-in via your own tools).
- **Single user, single machine.** The API is loopback-only by default; if you put a proxy in front of it, set
  `RAY_ALLOW_REMOTE_ORIGIN=1`.

---

## 5. Not implemented

- Mobile/remote access, multi-user accounts, sync.
- Local LLM inference and embeddings.
- OCR of scanned PDFs/images (text extraction only; images give metadata).
- Native Windows toast library (`win10toast` absent): notifications are written
  to the database and shown in the UI; a tray balloon is used when available.
- macOS/Linux desktop polish: the engine runs, the interface works, but Windows
  is the supported target.
- Auto-update.

---

## 6. Install, launch, use

```bat
:: Windows, from the project folder
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -WithDesktop -WithAutomation
.venv\Scripts\python.exe main.py
```

or double-click `RAY.bat`. The interface opens at `http://127.0.0.1:8765/`.

1. **Settings → AI providers** — install the Cursor CLI (`cursor.com/cli`, `cursor-agent login`) or paste a Gemini key
   (`aistudio.google.com/apikey`), Fish Audio key (`fish.audio`). Press **Test**
   for each; the result is a real API probe, not a placeholder.
2. Optional: Fish Audio voice id (`reference_id`), then **Voice → Load voices**.
3. **Knowledge** — add a folder to index; nothing leaves the machine.
4. **Security** — read the three permission levels, decide whether to enable
   shell commands (default off), set an idle lock if you want one.
5. Press **Ctrl+Space** and try: `check my laptop performance`, `add task Finish
   the notes`, `remind me to call the registrar at 6pm`, `find pdfs in downloads`.

Data folder: `%LOCALAPPDATA%\RAY`. Move or back it up as a unit; `secret.key`
lives there, so a copied folder keeps its encrypted credentials.

### Environment variables (optional)

`RAY_HOME`, `RAY_GROK_API_KEY` / `XAI_API_KEY`, `RAY_GEMINI_API_KEY` /
`GEMINI_API_KEY`, `RAY_FISH_API_KEY` / `FISH_AUDIO_API_KEY`. Values saved in the
UI take precedence. RAY does not read `.env` files.

---

## 7. Safety summary

- Destructive or privacy-sensitive actions require explicit approval; the
  approval row records the exact tool and arguments.
- Deletes are recoverable (quarantine + manifest); protected system paths refused.
- Shell execution is off by default, allow-listed, argument-list only, and
  rejects metacharacters.
- Documents are never uploaded unless you ask a document question *and* enable
  cloud documents.
- Credentials are encrypted, redacted from logs, never echoed by the API, and
  refused if pasted into settings, notes or memory.
- Local API: JSON-only writes, cross-site write rejection (also when network-bound),
  CSP, no wildcard CORS, path-traversal-safe static serving.
- Bounded work everywhere: retries, timeouts, daily budgets, step caps, task
  deadlines, cache and event-ring limits.

---

## 8. Known limitations / next steps

1. Run the Windows-only verifications in §3 and record the results.
2. Consider an OCR pass (Tesseract) for scanned legal PDFs.
3. Add semantic search later via a small local embedding model if the machine
   can afford it; keep FTS5 as the fallback.
4. Optionally schedule verified backups weekly (now manual).
5. The interface is dark-only; a light theme is a CSS variables change.
