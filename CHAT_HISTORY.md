# Chat history and progress log

Running record of the build sessions with Claude Code, so a new session can pick up where the last one stopped.
**Claude: read this file, `README.md`, `docs/decisions.md` and `docs/api_findings.md` before doing anything.**

Last updated: 2026-11-08

---

## 1. Project in one paragraph

Master's thesis (MDT999, Design Science Research): an agentic AI middleware between **Pixxi CRM** (UAE real estate) and Claude. Pixxi webhooks and a timer wake an orchestrator; agents read CRM data, reason with the LLM and write back AI-tagged notes and reminders; humans act on them in Pixxi. Source documents in this folder:

- `Agentic AI Middleware Architecture Flow, Rationale and Decisions.docx`: v3 architecture (components C1–C8, agents A1–A5, module M1).
- `README.md`: build spec, hard rules, 10-step build order, working agreement (one step at a time; ask before open decisions).
- Official Pixxi API: https://api-docs.pixxicrm.com, saved as `docs/pixxi_openapi.yaml`.

## 2. Where we are

| Build step (README §8) | Status |
| --- | --- |
| 1. Repo, environment, config, key handling | ✅ Done |
| 2. Pixxi API checks | ◐ Answered from official docs where possible (`docs/api_findings.md`); checks 1, 2, 3, 9 need a real account |
| 3. Contracts, write client, webhook receiver | ✅ Done (62 tests pass; live HTTP run verified) |
| **4. Seed dataset (10–20 leads) + Agent 1 via fixed-route orchestrator** | **⏭ NEXT. Needs an Anthropic API key** |
| 5–10 | Not started |

## 3. Decisions made by the user (do not re-ask)

- **LLM:** Claude (configurable in `src/config.py`). No Anthropic key yet.
- **D3:** hybrid orchestrator (LLM chooses, code enforces rules). **D4:** plain-timer scheduler. **D5:** no vector store.
- **Test bed:** no Pixxi account, so use a **mock that mirrors the official API as closely as possible**. Switch to the real API later through `.env` only.
- **Never use `PUT /lead/update`.** It exists in the real API (the proposal wrongly said it doesn't). Not using it is now a *design choice*: human oversight, PDPL, reassignment risk, and omitted fields get cleared.
- **Reminders:** create + read only (never `PUT /reminder` or `DELETE /reminder/{id}`).
- **Scope style:** build one step at a time and stop when its tests pass.
- **Git:** the user installs/configures git themselves. Git 2.56 is installed; the repo is initialised (`main`) and files are staged, but **nothing is committed** (no git user.name/email configured).

## 4. What exists in the repo

| Path | What |
| --- | --- |
| `src/config.py` | Settings from `.env` (pydantic-settings); secrets as SecretStr |
| `src/pixxi/http.py` | `PixxiHttp`: `X-PIXXI-TOKEN` header, envelope unwrap, `PixxiError`, 2 s read spacing, 429 retry, GET/POST only |
| `src/pixxi/models.py` | `Lead`, `ActivityLog`, `Listing`, `Agent`, `Reminder`, `MatchResult`, `Page`; parses `"min\|max"` ranges, Pixxi timestamps (Asia/Dubai), `Lead.scoring_view()` excludes nationality/gender |
| `src/pixxi/read_tools.py` | C6: list/iter leads, find_lead, activity logs, listings, single listing, agents, reminders, Pixxi matching. `READ_PATHS` allowlist |
| `src/pixxi/write_client.py` | C8: `add_note` (POST addLog/{AppName}), `create_reminder` (POST /reminder). `WRITE_PATHS` allowlist, write ledger, JSONL audit log |
| `src/audit/note.py` | `AgentNote`; `to_message()` = readable text + ASCII trailer `-- pixxi-agent-note/1 {json}`; `parse_message`, `is_ai_message` |
| `src/audit/store.py` | SQLite `MiddlewareStore`: write ledger, webhook dedup, webhook decision log |
| `src/ingest/webhook_receiver.py` | C3: `create_app()` factory; one route per event `/webhooks/pixxi/<slug>?key=SECRET`; ignores duplicates, AI-tagged notes/reminders, and UPDATE_LEADS within 120 s of our own write |
| `src/agents/base.py`, `src/orchestrator/state.py` | Contracts: `AgentResult`, `ReminderRequest`, `Agent` protocol, `LeadState` |
| `mock_pixxi/app.py` + `fixtures/` | Mock Pixxi mirroring the spec; worst-case webhooks; forbidden PUT/DELETE calls recorded; `/mock/*` endpoints simulate humans; Authorize button on /docs |
| `scripts/subscribe_webhooks.py` | Registers receiver callback URLs with Pixxi/mock (`--list`, `--unsubscribe`) |
| `tests/` | 62 tests, all in-process (`conftest.py` wires mock → receiver) |
| `docs/` | `pixxi_openapi.yaml`, `api_findings.md`, `decisions.md` (incl. "Thesis text to update") |

## 5. How to run (Windows, PowerShell, from this folder)

```
.venv\Scripts\python -m pytest -v                                                                   # tests
.venv\Scripts\python -m uvicorn mock_pixxi.app:app --port 8001                                      # terminal 1: mock Pixxi
.venv\Scripts\python -m uvicorn src.ingest.webhook_receiver:create_app --factory --port 8002         # terminal 2: receiver
.venv\Scripts\python -m scripts.subscribe_webhooks                                                  # terminal 3: connect them (redo after mock restart)
```

- Docs page: http://127.0.0.1:8001/docs. Click **Authorize** (top right), enter `mock-token`, click Authorize, then Close.
- Calling the mock from code: URL must include `/pixxiapi` (e.g. `/pixxiapi/v1/agent/list`) and the header is `X-PIXXI-TOKEN: mock-token`, **not** `Authorization: Bearer`. Most reads are POST with a JSON body.
- Simulate humans: `POST /mock/leads/{id}/notes`, `POST /mock/leads`; inspect `GET /mock/state`; reset `POST /mock/reset`.

## 6. Gotchas learned

- Python 3.11 is not installed; the venv uses **3.12** (`.venv`). Windows needs the `tzdata` package for Asia/Dubai.
- The venv's `python.exe` is a launcher that spawns a child process. Stop servers with `taskkill /PID <pid> /T /F`, not `Stop-Process`, or a stale server keeps the port (this caused 404s once).
- PowerShell 5.1 strips quotes from `python -c` arguments. Put longer snippets in a script file in the scratchpad instead.
- Starlette's TestClient uses `httpx2`, so code catches the project's own `PixxiError`, never httpx exception classes.
- Pixxi quirks: ids are sometimes strings, `communityId` can be `"null"`, `bedRooms` can be `-1`, listing pages have no page count, and lead activity-log pages use different key names (all handled in `models.py`).

## 7. Open items / next steps

1. **Step 4:** generate a 10–20 lead synthetic seed set (Pixxi schema, Table 3.4 distribution), build the C7 LLM service (versioned prompts in `src/llm/`), Agent 1 (intake + Layer 1 profile from the lead's `property` object and `GET /pixxiapi/v1/{propertyId}`), and a fixed-route LangGraph orchestrator fed by accepted webhook events. Done when a synthetic `ADD_LEADS` event produces a structured bootstrap note. **Needs `ANTHROPIC_API_KEY` in `.env`.**
2. When a Pixxi **test account with synthetic data only** exists: run the unverified checks listed at the bottom of `docs/api_findings.md`.
3. Update the thesis text per `docs/decisions.md` → "Thesis text to update" (notably: no-PUT is a design choice, not an API limit).
4. User to configure git identity and make the first commit.
5. Still unidentified: the "second flagged agent" (D6).

## 8. Session log

### Session 1 — 2026-11-08
- Read the architecture .docx and README. User chose: mock only, Claude (no key yet), accept D3–D5 defaults, step 1 first.
- **Step 1** built: venv, config, GET read tools, simple mock, docs. Installed Git via winget; repo initialised, not committed.
- User shared the official Pixxi API docs. Findings: `PUT /lead/update` exists, reads are POST, header `X-PIXXI-TOKEN`, notes via `addLog/{AppName}`, webhook subscribe API, no signatures. User decided: never PUT, reminders create+read, keep a spec-mirroring mock, then do step 3.
- **Realigned the whole repo with the official API** and **built step 3**. 62 tests pass; live run confirmed AI note events ignored and human note events accepted.
- Added an Authorize button to the mock's /docs page (the user hit 401s), and explained the `X-PIXXI-TOKEN` header and the `/pixxiapi` URL prefix.
- Created this file.
