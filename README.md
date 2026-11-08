# Pixxi Agents: Agentic AI Middleware for CRM Lead Management (PoC)

Proof-of-concept middleware that sits between Pixxi CRM (a UAE real estate SaaS CRM) and an LLM API. It watches lead events, reasons about them, and writes structured recommendations back to the CRM. It is the artefact of a master's research project (Design Science Research), so **every component must be explainable and every decision must be logged**.

This file is written for a coding agent (Claude Code) as well as for people. Read "Hard rules" and "Working agreement" before changing anything.

---

## 1. What the system does

1. Pixxi fires a webhook when a lead, note, reminder or listing is created or updated (`ADD_LEADS`, `UPDATE_LEADS`, `ADD_LEAD_NOTE`, `ADD_REMINDER`, `ADD_LISTINGS`, ...). A timer also wakes the system for work that has no event, such as finding dormant leads.
2. An orchestrator decides which agents should run.
3. Agents read CRM state through Pixxi's read-only endpoints (mostly `POST` with a filter body, e.g. `POST /pixxiapi/v1/lead/list`), reason through the LLM, and write results back as structured activity-log notes (`POST /pixxiapi/v1/lead/addLog/{AppName}`) and reminders (`POST /pixxiapi/v1/reminder`).
4. Real estate agents (humans) review the output in the Pixxi UI. Humans, not the system, execute any ownership change.
5. Agents read the activity logs (`POST /pixxiapi/v1/lead/activity-logs`) back to see what happened and re-plan.

The API reference is the official Pixxi spec, saved as `docs/pixxi_openapi.yaml`. Findings and open questions are in `docs/api_findings.md`.

"Agent" in this repo means an AI agent. Human sales staff are always called "real estate agents".

## 2. Hard rules (do not break these)

| Rule | Reason |
| --- | --- |
| **No PUT, PATCH or DELETE to Pixxi, ever.** Writes are only `POST /lead/addLog/{AppName}` (notes) and `POST /reminder` (create). Never `PUT /lead/update`, `PUT /reminder` or `DELETE /reminder/{id}`. | A **design choice**, not an API limit: Pixxi does offer these. Lead updates can reassign leads and clear omitted fields; humans keep authority (decisions.md). |
| **The system never contacts a lead.** Re-engagement produces drafts for a human to send. | PDPL compliance and the human-oversight commitment. |
| **The system never reassigns a lead.** It may post a reassignment suggestion; a human executes it in the Pixxi UI. | Same constraint. |
| **Every write is a tagged note** with `ai_generated: true`, agent ID, timestamp, input signals and rationale. | Audit requirement, and it lets dormancy checks ignore the system's own writes. |
| **Synthetic data only.** No real personal data in the repo, logs, prompts or LLM calls. | The proposal's ethics position (Section 3.18) rests on it. |
| **Scoring never uses nationality or gender.** | Bias safeguard stated in the proposal. |
| **Secrets stay in `.env`.** Never commit keys or print them. | Security. |
| **Do not invent Pixxi endpoints or fields.** If something is not in the Pixxi documentation, implement it in `mock_pixxi/` only and record it in `docs/api_findings.md` as unverified. | The real API is the ground truth. |

## 3. Architecture

Component IDs match the architecture diagram (blue = autonomous, grey = deterministic, dashed = future or optional).

| ID | Component | Type | Folder |
| --- | --- | --- | --- |
| C1 | Scheduler | Deterministic | `src/ingest/scheduler.py` |
| C2 | Capture adapter (SaaS notes) | Future, stub only | not built in PoC |
| C3 | Webhook receiver (FastAPI) | Deterministic | `src/ingest/webhook_receiver.py` |
| C4 | Orchestrator (supervisor) | Autonomous | `src/orchestrator/` |
| C5 | Checkpoint store (SQLite) | Deterministic | `src/orchestrator/checkpoint.py` |
| C6 | Read tools (read-only calls) | Deterministic | `src/pixxi/read_tools.py` (+ `src/pixxi/http.py`) |
| C7 | LLM service (LangChain + Claude) | Deterministic | `src/llm/` |
| C8 | Write client (two POSTs only) | Deterministic | `src/pixxi/write_client.py` |
| A1 to A5 | Agents 1 to 5 | Autonomous | `src/agents/` |
| M1 | Response-prioritisation module | Autonomous | `src/modules/prioritisation.py` |
| S2 | Vector store (Chroma) | Optional, undecided | not built unless approved |

### Agents (new numbering)

| Agent | Name | Does | Needs |
| --- | --- | --- | --- |
| 1 | Lead intake and profile bootstrap | Builds a Layer 1 profile from portal fields and the listing (the lead's `property` object, then `GET /pixxiapi/v1/{propertyId}`); posts a baseline note and a prompt asking the real estate agent to log confirmed details. | Nothing else. |
| 2 | Lead qualification (conditional) | Extracts confirmed intent, budget, purchase timeline, financing status, motivation and flexibility from logged notes into a structured qualification object. | At least one human-written note. |
| 3 | Scoring and re-prioritisation | Scores from Layer 1 data, then re-scores from the confirmed profile (Layer 2 weighted higher). Calls M1 for priority and timing. | Agent 1; Agent 2 for a full score. |
| 4 | Lead re-engagement | Finds dormant leads (no human activity or reminder for 30 days, configurable), drafts a personalised message for a human to send. Calls M1 for timing. | Scheduler wake-up. |
| 5 | Property recommendation | Forward match (confirmed profile against listings, budget tolerance plus or minus 10 percent) and reverse match (new listing against existing leads). | Layer 2 for forward match; Layer 1 is enough for reverse match. |

### Module M1: response prioritisation

A reasoning service (one LLM call with a structured prompt), not an agent. It has no goal loop and never writes to Pixxi; the calling agent writes the result.

- **Input:** lead score, enquiry source, assigned real estate agent's workload, interaction recency (`createTime`, `updateTime`, `reminderCount`).
- **Output:** priority tier (urgent, standard, low), contact window, written rationale, and an optional reassignment suggestion.
- **Reassignment:** the trigger is reasoned from the interaction between lead priority and the agent's queued high-priority leads, not a fixed lead count. Output is a suggestion only.

### Orchestrator

LangGraph stays as the runtime. The next step is chosen by a supervisor LLM, while code enforces facts that are not judgments: Agent 2 needs notes, forward matching needs Layer 2, nothing is sent, nothing is reassigned. Agents may return `requests` (for example "agent_3: rescore"); the orchestrator decides whether to honour them. Start with a fixed route to get Agent 1 working, then replace it with the supervisor.

### Terminology

- **Layer 1 / Layer 2** are data layers: Layer 1 is the lead hypothesis from portal data and listing; Layer 2 is the confirmed profile after a human logs notes.
- **Tiers** are architecture levels (ingestion, orchestration, execution, services and I/O). Never call these "layers".

## 4. Repository layout

```text
pixxi-agents/
  README.md
  pyproject.toml            # dependencies + pytest config (managed with uv)
  uv.lock
  .env.example              # PIXXI_TOKEN, WEBHOOK_SECRET, ANTHROPIC_API_KEY (never committed)
  scripts/                  # subscribe_webhooks.py, check_llm.py, run_step4_demo.py, dev_up.ps1
  src/
    config.py               # model name, dormancy days, thresholds, paths
    main.py                 # wires receiver -> orchestrator -> agents (uvicorn src.main:create_app --factory)
    pixxi/                  # C6 read tools, C8 write client, API models (httpx)
    ingest/                 # C3 webhook receiver, C1 scheduler
    orchestrator/           # C4 supervisor, LangGraph graph, C5 checkpoint
    agents/                 # intake, qualification, scoring, reengagement, recommendation
    modules/                # M1 prioritisation
    llm/                    # C7 service, prompt templates (versioned)
    audit/                  # structured note format and logging
  mock_pixxi/               # fake Pixxi server and fixtures
  ui/                       # live Streamlit dashboard (viewer + mock simulation; not part of C1-C8)
  data/synthetic/           # lead generator, annotated notes, ground truth (seed/: 16-lead step 4 set)
  eval/                     # scenarios, metric scripts, run logs
  docs/
    pixxi_openapi.yaml      # official Pixxi API spec (ground truth for tests)
    api_findings.md         # results of the Pixxi API checks
    decisions.md            # decisions and their reasons
  tests/
```

## 5. Stack

Python 3.11, LangChain, LangGraph, FastAPI with uvicorn, httpx, SQLite (LangGraph checkpointer), pydantic, pytest. LLM: Claude by default, set in `src/config.py` so it can be swapped. The proposal allows Claude or GPT-4. FastAPI, httpx and SQLite come from the design diagram and are not named in the proposal.

A local model may be used for development and for generating synthetic notes. **Final evaluation runs must use the declared model.** Do not tune prompts only on a local model.

## 6. Shared contracts

Fix these first. Agents are built against them.

**Activity Log note** (`src/audit/note.py`)

Pixxi stores a note as a plain `message` string, so the note is rendered as readable text followed by one ASCII trailer line carrying this JSON:

```text
[AI agent_3_scoring] Score 78 (high confidence)
Rationale: Confirmed budget and timeline in note of 3 Nov; off-plan investor profile.
-- pixxi-agent-note/1 {"schema":"pixxi-agent-note/1","ai_generated":true,...}
```

Reminders carry `[AI]` at the start of the title and the same note in the description.

```json
{
  "schema": "pixxi-agent-note/1",
  "ai_generated": true,
  "agent_id": "agent_3_scoring",
  "run_id": "scn04-0017",
  "lead_id": 842115,
  "timestamp": "2026-11-09T09:30:00Z",
  "summary": "Score 78 (high confidence)",
  "input_signals": {"layer": 2, "budget_tier": "high", "source": "portal"},
  "decision": {"score": 78, "confidence": "high"},
  "rationale": "Confirmed budget and timeline in note of 3 Nov; off-plan investor profile."
}
```

**Lead state**

```python
class LeadState(TypedDict):
    lead_id: int                      # Pixxi lead IDs are integers
    layer: Literal[1, 2]              # data layer, not architecture tier
    profile: dict                     # Agent 1 and Agent 2 output
    score: dict | None                # Agent 3 output
    priority: dict | None             # M1 output: tier, window, rationale
    last_human_activity: datetime     # ignores ai_generated notes
    pending_requests: list[str]       # e.g. "agent_3: rescore"
```

**Agent interface**

```python
class AgentResult(TypedDict):
    notes: list[AgentNote]            # posted through the write client
    reminders: list[Reminder]
    state_updates: dict
    requests: list[str]               # asks to the orchestrator

def run(state: LeadState, tools: Tools) -> AgentResult: ...
```

Agents never call the write client directly; the orchestrator posts what they return. This keeps one audited exit point.

## 7. Verify first: Pixxi behaviours not yet known

Do these before building on them. Record every result in `docs/api_findings.md`. Status: checks 4, 5, 6, 7 and 8 are answered from the official docs (fully or partly); checks 1, 2, 3 and 9 need a real Pixxi account and are assumed worst case in the mock.

| # | Question | Affects |
| --- | --- | --- |
| 1 | Does `POST /lead/addLog` fire an `UPDATE_LEADS` webhook? | Loop guard in C3 |
| 2 | Does `POST /reminder` fire a webhook or change `updateTime` or `reminderCount`? | Loop guard; dormancy |
| 3 | Does a note change `updateTime`? | Dormancy definition |
| 4 | Can a reminder be read, cancelled or replaced? | M1 and orchestrator re-planning |
| 5 | Does `POST /lead/list` paginate and filter by `updateTime` or assignee? | Scheduler sweeps; workload |
| 6 | Can agent profiles and workload be read? | M1 workload input |
| 7 | Do webhooks retry, arrive in order, or carry a signature? | C3 de-duplication and security |
| 8 | Is there a sandbox, or only the live API? | Test bed |
| 9 | Are there length or format limits on notes? | Note format |

Until check 1 and 2 are answered, assume the worst: the receiver must ignore any event caused by a tagged write.

## 8. Build order

Each step is done only when its test passes.

1. Repository, environment, config, key handling. **Done when** tests run and one GET call returns data from the real API or the mock.
2. Run the checks in section 7. **Done when** each has a recorded result.
3. Contracts, write client with the AI tag, and webhook receiver with the self-write filter. **Done when** a posted note is tagged and the receiver ignores the event it caused.
4. Seed dataset (10 to 20 leads matching the real Pixxi schema), then Agent 1 through a fixed-route orchestrator. **Done when** a synthetic `ADD_LEADS` event produces a structured bootstrap note.
5. Full synthetic dataset (100 leads, annotated notes, ground truth) and the first evaluation scenario. **Done when** all 100 leads load into the mock and one scenario replays.
6. Agent 2 and the SQLite checkpoint store. **Done when** precision and recall run on annotated leads.
7. Agent 3 and M1. **Done when** scores and priority tiers appear with rationales.
8. Supervisor orchestrator, scheduler, Agent 4. **Done when** a dormant lead is found by the timer and a draft is written.
9. Agent 5, then all 12 scenarios. **Done when** every scenario runs without manual steps.
10. Evaluation runs on 100 leads, with and without M1.

The synthetic dataset follows the proposal's Table 3.4: 12 nationalities; 63 percent off-plan apartments; enquiry sources portal 45, referral 22, social media 18, exhibition 10, cold call 5 percent; stage mix new 30, contacted 35, qualified 20, dormant 15 percent; 60 high-compliance and 40 low-compliance leads (no agent notes). Generate each note from known attributes, so ground-truth labels exist by construction.

## 9. Logging for evaluation

Log from the first run. Every log row carries `run_id`, `scenario_id`, model name and version, prompt version, temperature, token cost and latency. Also log:

| Metric | Threshold | Log |
| --- | --- | --- |
| Qualification accuracy | 80 percent precision and recall | Agent 2's qualification object next to ground truth |
| Response timing | Recommended window of 4 hours or less for high-intent leads, against a 24 to 72 hour baseline | M1 tier, window, rationale, `createTime`, intent level |
| Re-engagement | 75 percent of dormant leads rated 4 of 5 or higher | Draft, dormancy signal, profile fields used, empty column for the human rubric score |
| Recommendation relevance | 85 percent within tolerance; 90 percent relevant by human review | Ranked list with budget, type, location and tolerance-check result |

A flag marks runs with and without M1. The time-to-first-contact metric cannot be measured on synthetic data (the system cannot contact leads), so compare the recommended window with the baseline. API budget for the full evaluation is about USD 20.

## 10. Working agreement for the coding agent

- **Ask before deciding the open questions below.** Do not pick for the user.
- Work one build step at a time and stop when its test passes. Do not build ahead.
- Write the test first or alongside the code. Run `pytest` before saying a step is done.
- Use type hints and pydantic models at every boundary (API, notes, state).
- Keep prompts in versioned files under `src/llm/`. Never inline long prompts in code.
- Set the model temperature low and record it.
- Keep `mock_pixxi/` separate from `src/`. The middleware must work against the mock or the real API through configuration alone.
- Record decisions and their reasons in `docs/decisions.md`, in plain language, because every design choice must be explained in the thesis.
- If a change conflicts with the hard rules in section 2, stop and ask.
- Small, focused commits. Do not commit `.env`, generated data with real values, or large logs.

## 11. Open decisions

Ask the user before touching these.

| ID | Decision | Default until told otherwise |
| --- | --- | --- |
| Model | Declared model for the evaluation: Claude or GPT-4 | Switchable; dev uses OpenAI gpt-4.1-mini (decided 2026-11-09), declared model still open |
| D3 | Orchestrator: hybrid (LLM chooses, code enforces rules) or fully LLM-driven | Hybrid |
| D4 | Scheduler: plain timer or a reasoning monitor | Plain timer |
| D5 | Vector store S2 for property search: keep, drop, or adopt | Not built |
| Test bed | Real Pixxi API, mock server, or both | Mock mirroring the official spec until a test account exists (decided 2026-11-08) |
| Agent flagged | A second agent was flagged as non-agentic and is still unidentified | None |

## 12. Known inconsistencies in the source documents

The research proposal still describes the older design. Do not copy these into code:

- The proposal's Table 3.3 lists re-engagement as triggered by `UPDATE_LEADS`. A dormant lead produces no event, so it must be triggered by the scheduler.
- The proposal's Section 3.16 describes the orchestrator as a StateGraph with conditional edges. The design is now a supervisor.
- The proposal has seven agents with a separate Agent 4 (response timing). Agent 4 is now module M1 and the agents are renumbered 1 to 5.
- The proposal names no vector store; Chroma appeared only in an earlier diagram.



# private notes

uv run python -m scripts.run_step4_demo
