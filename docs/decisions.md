# Decisions

Plain-language record of design decisions and why they were made, for the thesis write-up.

| Date | ID | Decision | Reason |
| --- | --- | --- | --- |
| 2026-11-08 | Model | Claude for the PoC, set in `src/config.py` (`LLM_MODEL`) | Matches the architecture diagram. Keeping it in config means the proposal's GPT-4 option stays open without code changes. |
| 2026-11-08 | D1 | Old Agent 4 becomes module M1 | Settled in the architecture note. Removes the overlap between scoring/re-prioritisation and response prioritisation. |
| 2026-11-08 | D2 | Agents renumbered 1 to 5 | Settled in the architecture note. |
| 2026-11-08 | D3 | Hybrid orchestrator: a supervisor LLM picks the next agent, and code enforces the hard rules | Rules such as "forward matching needs Layer 2" and "nothing is sent" are facts, not judgments. Code makes them reliable and auditable, and separates agent mistakes from missing guardrails across the 100-lead evaluation. |
| 2026-11-08 | D4 | Scheduler is a plain timer that only wakes the orchestrator | Cheap and testable; the orchestrator already does the reasoning. A reasoning monitor would add a second reasoner and more LLM cost. |
| 2026-11-08 | D5 | No vector store (S2 not built) | Section 3.12 describes a ±10% tolerance-band algorithm; the LLM adds ranking and rationale on top. Embeddings would only help free-text preferences. |
| 2026-11-08 | Python | Python 3.12 instead of 3.11 | 3.12 is installed and fully supported by LangChain/LangGraph. No 3.12-only features are used. |
| 2026-11-08 | Repo root | The `Implementation` folder is the repository root | The README and source documents already live there. |
| 2026-11-08 | API source | The official Pixxi spec (`docs/pixxi_openapi.yaml`) is the ground truth; tests check the mock and the client against it | Replaces the guessed endpoints from step 1. Any drift between mock and spec now fails a test. |
| 2026-11-08 | Test bed | A mock that mirrors the official spec (paths, methods, header, envelope, field names, timestamps, rate limits) | No Pixxi account exists. Switching to the real API is a `.env` change (`PIXXI_MODE`, `PIXXI_BASE_URL`, `PIXXI_TOKEN`). The mock also keeps the 100-lead evaluation free, fast and repeatable within the 2,000 req/h limit. |
| 2026-11-08 | No lead updates | The middleware **never** calls `PUT /lead/update`, even though the API offers it | **Correction:** the proposal and architecture note say Pixxi has no PUT/PATCH on leads. The official docs show it does. Not updating leads is therefore a **design choice**, not an API constraint. Reasons: (1) human oversight and PDPL: agents decide and recommend, humans execute; (2) the endpoint can reassign (`agentId`), which must stay a human act; (3) omitted fields are cleared, so a partial update silently destroys data; (4) a POST-only exit point is easier to audit. |
| 2026-11-08 | Reminders | Create and read only; never update or delete | The API allows both, but editing or deleting could remove human-created reminders or hide the system's earlier decisions from the audit trail. Re-planning creates a new reminder with a rationale instead. |
| 2026-11-08 | Note format | The JSON note is rendered as readable text plus one ASCII trailer line `-- pixxi-agent-note/1 {json}` in the `message` string | Pixxi stores notes as plain strings. Real estate agents read the first lines; the trailer lets the system recognise its own writes (loop guard, dormancy) and keeps the full audit record inside Pixxi. |
| 2026-11-08 | Webhooks | One callback URL per event, protected by a shared secret in the query string (`?key=`) | Pixxi webhook bodies do not include the event name, and no signature is documented. |
| 2026-11-08 | Loop guard | The receiver ignores AI-tagged notes and reminders, and ignores `UPDATE_LEADS` on a lead the system wrote to within 120 s (configurable) | Worst-case assumption until checks 1–3 are verified. Trade-off: a human's lead update within that window is also suppressed, but their note still arrives through `ADD_LEAD_NOTE` (tested). |
| 2026-11-08 | Timezone | Pixxi timestamps are read as Asia/Dubai | The API sends no timezone. To verify on first real access. |
| 2026-11-08 | Agent 2 trigger (proposal) | `ADD_LEAD_NOTE` is the natural trigger for qualification | The event exists in the official API and fires exactly when a human logs a note. To confirm when building Agent 2. |

## Thesis text to update

- **Sections 3.x / architecture note, step 6 and "Autonomy is separated from authority":** replace "forced by the missing PUT and PATCH endpoints" with "a deliberate design choice; Pixxi does offer `PUT /lead/update`, which the system never uses because...". The argument gets stronger: oversight is chosen, not imposed.
- **Architecture note, step 4 / C6:** reads are "read-only calls", mostly POST with a filter body, not "GET calls".
- **Endpoint names:** `/lead/addLog/{AppName}` (not `/activities`), `/lead/activity-logs`, `/reminder`, `/properties`.
- **Table 3.3 triggers:** add `ADD_LEAD_NOTE` (Agent 2) and `ADD_REMINDER`. Re-engagement stays on the scheduler.
- **Agent 1:** the interested listing comes from the lead's `property` object, not `propertyReference`.
- **Agent 5 evaluation:** Pixxi's own matching endpoint can serve as a baseline for recommendation relevance.
