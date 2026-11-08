# Chat history and progress log

Running record of the build sessions with Claude Code, so a new session can pick up where the last one stopped.
**Claude: read this file, `README.md`, `docs/decisions.md` and `docs/api_findings.md` before doing anything.**

Last updated: 2026-11-09

---

## 1. Project in one paragraph

Master's thesis (MDT999, Design Science Research): an agentic AI middleware between **Pixxi CRM** (UAE real estate) and an LLM. Pixxi webhooks and a timer wake an orchestrator; agents read CRM data, reason with the LLM and write back AI-tagged notes and reminders; humans act on them in Pixxi. Source documents in this folder:

- `Agentic AI Middleware Architecture Flow, Rationale and Decisions.docx`: v3 architecture (components C1–C8, agents A1–A5, module M1).
- `README.md`: build spec, hard rules, 10-step build order, working agreement (one step at a time; ask before open decisions).
- Official Pixxi API: https://api-docs.pixxicrm.com, saved as `docs/pixxi_openapi.yaml`.

## 2. Where we are

| Build step (README §8) | Status |
| --- | --- |
| 1. Repo, environment, config, key handling | ✅ Done |
| 2. Pixxi API checks | ◐ Answered from official docs where possible (`docs/api_findings.md`); checks 1, 2, 3, 9 need a real account |
| 3. Contracts, write client, webhook receiver | ✅ Done (live HTTP run verified) |
| 4. Seed dataset (16 leads) + Agent 1 via fixed-route orchestrator | ✅ Done (88 offline tests + 1 live test pass; demo on 5 arrivals with real `gpt-4.1-mini`) |
| **5. Full synthetic dataset (100 leads, annotated notes, ground truth) + first evaluation scenario** | **⏭ NEXT** |
| 6–10 | Not started |

## 3. Decisions made by the user (do not re-ask)

- **LLM:** switchable provider (`LLM_PROVIDER=openai|anthropic`). Dev uses OpenAI `gpt-4.1-mini`. The user has an OpenAI key with only **~$5**, so **minimise real calls** (token cap, per-process call limit, live tests only on request). The declared model for the final evaluation is still open; decide before step 10. No Anthropic key.
- **No fake LLM** (user, 2026-11-09): tests that need the model use the real one and are marked `live` (`uv run pytest -m live`); everything else is tested without any LLM.
- **D3:** hybrid orchestrator (LLM chooses, code enforces rules). **D4:** plain-timer scheduler. **D5:** no vector store.
- **Test bed:** no Pixxi account, so use a **mock that mirrors the official API as closely as possible**. Switch to the real API later through `.env` only.
- **Never use `PUT /lead/update`.** It exists in the real API (the proposal wrongly said it doesn't). Not using it is a *design choice*: human oversight, PDPL, reassignment risk, and omitted fields get cleared.
- **Reminders:** create + read only (never `PUT /reminder` or `DELETE /reminder/{id}`).
- **Scope style:** build one step at a time and stop when its tests pass.
- **Package manager:** uv (`pyproject.toml` + `uv.lock`), not pip.
- **Git:** the user installs/configures git themselves. Git 2.56 is installed; the repo is initialised (`main`) but **nothing is committed** (no git user.name/email configured).

## 4. What exists in the repo

| Path | What |
| --- | --- |
| `src/config.py` | Settings from `.env` (pydantic-settings); secrets as SecretStr. LLM: provider, model, temperature, `LLM_MAX_TOKENS`, `LLM_MAX_CALLS_PER_PROCESS`, prices for the cost estimate |
| `src/pixxi/http.py` | `PixxiHttp`: `X-PIXXI-TOKEN` header, envelope unwrap, `PixxiError`, 2 s read spacing, 429 retry, GET/POST only |
| `src/pixxi/models.py` | `Lead`, `ActivityLog`, `Listing`, `Agent`, `Reminder`, `MatchResult`, `Page`; parses `"min\|max"` ranges, Pixxi timestamps (Asia/Dubai), `Lead.scoring_view()` excludes nationality/gender |
| `src/pixxi/read_tools.py` | C6: list/iter leads, find_lead, activity logs, listings, single listing, agents, reminders, Pixxi matching. `READ_PATHS` allowlist |
| `src/pixxi/write_client.py` | C8: `add_note` (POST addLog/{AppName}), `create_reminder` (POST /reminder). `WRITE_PATHS` allowlist, write ledger, JSONL audit log |
| `src/audit/note.py` | `AgentNote`; `to_message()` = readable text (+ optional `action_items` bullet list) + ASCII trailer `-- pixxi-agent-note/1 {json}`; `parse_message`, `is_ai_message` |
| `src/audit/store.py` | SQLite `MiddlewareStore`: write ledger, webhook dedup, webhook decision log |
| `src/ingest/webhook_receiver.py` | C3: `create_app()` factory; one route per event `/webhooks/pixxi/<slug>?key=SECRET`; ignores duplicates, AI-tagged notes/reminders, and UPDATE_LEADS within 120 s of our own write |
| `src/agents/base.py`, `src/orchestrator/state.py` | Contracts: `AgentResult`, `ReminderRequest`, `Agent` protocol, `LeadState` |
| `src/llm/service.py` | C7 `LLMService`: provider from config, structured output (pydantic via function calling), spend guard, logs every call to `eval/runs/llm_calls.jsonl` (tokens, cost, latency, prompt version) |
| `src/llm/prompts/` | Versioned prompt files, never edited once used: `agent_1_intake_v1.md` (superseded), `agent_1_intake_v2.md` (current) |
| `src/agents/intake.py` | Agent 1: code computes portal facts, listing price-vs-budget check (±10 %), missing fields; LLM returns `IntakeHypothesis`; returns one note whose `action_items` are questions for the real estate agent. Skips leads it already bootstrapped. The LLM never sees name/contact/nationality/gender |
| `src/orchestrator/graph.py` | C4 fixed route (`ADD_LEADS` → Agent 1) as LangGraph: route → run_agents → commit. Commit checks lead/agent of every write; an error writes nothing. Logs `eval/runs/orchestrator_runs.jsonl` |
| `src/main.py` | `create_app()` = receiver wired to the orchestrator (`uvicorn src.main:create_app --factory`) |
| `mock_pixxi/app.py` + `fixtures/` | Mock Pixxi mirroring the spec; worst-case webhooks; forbidden PUT/DELETE calls recorded; `/mock/*` endpoints simulate humans; Authorize button on /docs. `MOCK_FIXTURES_DIR` (or `fixtures_dir=`) loads another dataset |
| `data/synthetic/generate_seed.py` + `seed/` | Deterministic seed set: `leads.json` (11 existing), `arrivals.json` (5 new → ADD_LEADS), `listings.json` (14), `agents.json`, `ground_truth.json` |
| `scripts/subscribe_webhooks.py` | Registers receiver callback URLs with Pixxi/mock (`--list`, `--unsubscribe`) |
| `scripts/check_llm.py` | One tiny LLM call to check the API key works |
| `scripts/run_step4_demo.py` | In-process demo: seed mock + receiver + orchestrator + Agent 1 with the real LLM; prints each note and the cost |
| `pyproject.toml`, `uv.lock` | Pinned dependencies, pytest config (`live` marker deselected by default) |
| `tests/` | 88 offline tests + 1 `live` test. `conftest.py` wires mock → receiver; `make_pipeline` wires seed mock → receiver → orchestrator |
| `docs/` | `pixxi_openapi.yaml`, `api_findings.md`, `decisions.md` (incl. "Thesis text to update") |

## 5. How to run (Windows, PowerShell, from this folder)

```
uv sync                                         # create/update .venv from uv.lock
uv run pytest -v                                # offline tests (free, no LLM)
uv run pytest -m live -s                        # live test: 1 real LLM call (~$0.001)
uv run python -m scripts.check_llm              # is the API key OK? (~13 tokens)
uv run python -m scripts.run_step4_demo         # step 4 demo, 5 new leads (~$0.004); --limit 1 or --lead 910008
uv run python data/synthetic/generate_seed.py   # regenerate the seed set (deterministic)
```

Three-terminal version over real HTTP (agents run on each accepted webhook; LLM calls cost credit):

```
$env:MOCK_FIXTURES_DIR="data/synthetic/seed"; uv run uvicorn mock_pixxi.app:app --port 8001   # terminal 1: mock Pixxi
uv run uvicorn src.main:create_app --factory --port 8002                                       # terminal 2: receiver + orchestrator
uv run python -m scripts.subscribe_webhooks                                                    # terminal 3: connect them, then POST a lead to /mock/leads
```

`src.ingest.webhook_receiver:create_app` still works as a receiver that only records events (no agents).

- Dependencies live in `pyproject.toml` (pinned; pytest in the `dev` group) with `uv.lock`. Add packages with `uv add <pkg>` / `uv add --dev <pkg>`, never pip.
- Docs page: http://127.0.0.1:8001/docs. Click **Authorize** (top right), enter `mock-token`, click Authorize, then Close.
- Calling the mock from code: URL must include `/pixxiapi` (e.g. `/pixxiapi/v1/agent/list`) and the header is `X-PIXXI-TOKEN: mock-token`, **not** `Authorization: Bearer`. Most reads are POST with a JSON body.
- Simulate humans: `POST /mock/leads/{id}/notes`, `POST /mock/leads`; inspect `GET /mock/state`; reset `POST /mock/reset`.
- Logs: `eval/runs/llm_calls.jsonl`, `eval/runs/orchestrator_runs.jsonl`, `data/audit/writes.jsonl` (all git-ignored).

## 6. Gotchas learned

- Python 3.11 is not installed; the venv uses **3.12** (`.venv`). Windows needs the `tzdata` package for Asia/Dubai.
- The venv's `python.exe` is a launcher that spawns a child process. Stop servers with `taskkill /PID <pid> /T /F`, not `Stop-Process`, or a stale server keeps the port (this caused 404s once).
- PowerShell 5.1 strips quotes from `python -c` arguments. Put longer snippets in a script file in the scratchpad instead.
- Long Bash heredocs containing Python triple-quoted strings sometimes fail to parse in this shell; use the Write/Edit tools for multi-line file content.
- Starlette's TestClient uses `httpx2`, so code catches the project's own `PixxiError`, never httpx exception classes.
- `Settings.model_copy(update=...)` skips validation: pass secrets as `SecretStr(...)`, not plain strings.
- `AgentNote` uses the alias `schema`; dump with `by_alias=True` whenever the dict is validated again (bit us in the orchestrator).
- Pixxi quirks: ids are sometimes strings, `communityId` can be `"null"`, `bedRooms` can be `-1`, listing pages have no page count, and lead activity-log pages use different key names (all handled in `models.py`).

## 7. Open items / next steps

1. **Step 5:** full synthetic dataset (100 leads, Table 3.4 proportions, annotated human notes generated from known attributes so ground truth exists by construction) and the first evaluation scenario. Done when all 100 leads load into the mock and one scenario replays. Extend `data/synthetic/generate_seed.py` (or add a sibling generator) rather than starting over. **Ask the user** whether notes are generated by template (free) or by the LLM (costs credit).
2. Agent 1 prompt v2 sometimes adds a generic filler question ("any specific amenities?"). Harmless; revisit if the evaluation shows it matters.
3. `README.md` §5 still says Python 3.11 (project uses 3.12, see decisions.md); the user has not said yet whether to change it.
4. When a Pixxi **test account with synthetic data only** exists: run the unverified checks listed at the bottom of `docs/api_findings.md`.
5. Update the thesis text per `docs/decisions.md` → "Thesis text to update" (notably: no-PUT is a design choice, not an API limit).
6. User to configure git identity and make the first commit.
7. Still unidentified: the "second flagged agent" (D6).
8. Declared LLM for the final evaluation (OpenAI vs Claude) is still open; decide before step 10.

## 8. Session log

### Session 1 — 2026-11-08
- Read the architecture .docx and README. User chose: mock only, Claude (no key yet), accept D3–D5 defaults, step 1 first.
- **Step 1** built: venv, config, GET read tools, simple mock, docs. Installed Git via winget; repo initialised, not committed.
- User shared the official Pixxi API docs. Findings: `PUT /lead/update` exists, reads are POST, header `X-PIXXI-TOKEN`, notes via `addLog/{AppName}`, webhook subscribe API, no signatures. User decided: never PUT, reminders create+read, keep a spec-mirroring mock, then do step 3.
- **Realigned the whole repo with the official API** and **built step 3**. 62 tests pass; live run confirmed AI note events ignored and human note events accepted.
- Added an Authorize button to the mock's /docs page (the user hit 401s), and explained the `X-PIXXI-TOKEN` header and the `/pixxiapi` URL prefix.
- Created this file.

### Session 2 — 2026-11-09
- Migrated from pip to **uv**: `requirements.txt` + `pytest.ini` replaced by `pyproject.toml` (Python 3.12 only, `package = false`, pytest in the `dev` group, `[tool.pytest.ini_options]`) and `uv.lock`.
- User has an OpenAI key (~$5 credit) and no Anthropic key. Decided: provider switchable, dev on `gpt-4.1-mini`, declared evaluation model still open. Added `langchain-openai` and the LLM settings. Checked the key with `scripts/check_llm.py` (1 call, 13 tokens).
- **Built step 4** (user asked for no fake LLM; use the real one):
  - Seed set: 16 leads (11 existing + 5 arrivals), 14 listings, ground truth; deterministic generator; the mock can load it.
  - C7 `LLMService` with versioned prompt files, structured output, call log and spend guard. Agent 1 (facts by code, hypothesis and questions by the LLM, idempotent). Fixed-route LangGraph orchestrator with a checking commit node. `src/main.py` wires receiver → orchestrator.
  - `AgentNote` gained optional `action_items` (questions for the real estate agent); backward compatible.
  - A live run of prompt v1 on the 5 arrivals showed re-asked facts, mortgage questions for a rental lead, and "Studio-1" shown as "1BR". Wrote prompt **v2** (v1 kept) plus a code fix so payment method and investor status are not "missing" for RENT leads. Re-run: all 5 notes good; 910008 correctly flagged as enquiring about a listing above budget.
  - Results: 88 offline tests + 1 live test pass. Demo: 5 notes, 0 forbidden calls, all self-caused webhooks ignored.
  - Total OpenAI spend this session ≈ **$0.01** (one Agent 1 call ≈ $0.0008, about 1.1K tokens).
  - Decisions recorded in `docs/decisions.md` (seed dataset, Agent 1 design, LLM inputs, confirmation prompt, idempotency, orchestrator, prompt versioning, prompt v2, spend control). `data/audit/` added to `.gitignore`.
