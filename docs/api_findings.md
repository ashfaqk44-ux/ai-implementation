# Pixxi API findings

Source: the official Pixxi Data API documentation (https://api-docs.pixxicrm.com), saved as
`docs/pixxi_openapi.yaml` on 2026-11-08 (OpenAPI 3.1, 33 paths, 10 webhook events).
There is **no Pixxi account yet**, so nothing below has been tested against the live API.
"Documented" means the spec says so; "unverified" means the spec is silent and the mock
assumes the worst case.

## Basics (documented)

| Item | Value |
| --- | --- |
| Base URL | `https://dataapi.pixxicrm.ae` |
| Auth | Header `X-PIXXI-TOKEN` (Pixxi CRM > Admin > Integrations > Pixxi Forms) |
| Envelope | `{"statusCode": 200, "message": "success", "data": ...}` |
| Rate limits | 2,000 requests/hour per token; at least 2 s between requests (create/update requests exempt); page size ≤ 500; `429 Too Many Requests` when exceeded |
| Timestamps | Strings `yyyy-MM-dd HH:mm:ss`, **no timezone** (we assume Asia/Dubai; unverified) |
| Reads | Mostly `POST` with a JSON filter body, e.g. `POST /pixxiapi/v1/lead/list` |

## Endpoints the middleware uses

| Purpose | Endpoint | Used by |
| --- | --- | --- |
| Leads (paged, filter status/clientType/search, sort ID/CREATE_TIME/UPDATE_TIME) | `POST /pixxiapi/v1/lead/list` | C6 |
| Lead activity logs (filter type Note/Contact/Viewing/System, date range) | `POST /pixxiapi/v1/lead/activity-logs` | C6 |
| Website listings (filters) | `POST /pixxiapi/v1/properties` | C6 |
| Single listing | `GET /pixxiapi/v1/{propertyId}` | C6 |
| Agents | `GET /pixxiapi/v1/agent/list` | C6 |
| Reminders (paged) | `POST /pixxiapi/v1/reminder/list` | C6 |
| Pixxi's own lead-to-listing matching | `POST /pixxiapi/v1/matching/listings/passive/page` | C6 (baseline for Agent 5) |
| Add note | `POST /pixxiapi/v1/lead/addLog/{AppName}` `{leadId, message}` | C8 |
| Create reminder | `POST /pixxiapi/v1/reminder` | C8 |
| Webhook subscription | `POST /pixxiapi/webhook/v1/subscribe`, `/unsubscribe`, `GET /subscription/list` | `scripts/subscribe_webhooks.py` |

## Endpoints that exist but the middleware never calls (by design)

| Endpoint | Why not |
| --- | --- |
| `PUT /pixxiapi/v1/lead/update/{AppName}` | It can overwrite any lead field and change `agentId` (reassignment). Omitted fields such as `gender`, `birthdate` and `moveInDate` are **cleared**, so a partial update destroys data. Human oversight is a design commitment. See decisions.md. |
| `PUT /pixxiapi/v1/reminder`, `DELETE /pixxiapi/v1/reminder/{id}` | Reminders are create + read only (decisions.md). |
| `/integration/v1/property*` | Listing management; needs extra permission and is not needed. |

## The checks from README section 7

| # | Question | Answer | Status | Mock behaviour |
| --- | --- | --- | --- | --- |
| 1 | Does `POST addLog` fire `UPDATE_LEADS`? | Docs say `ADD_LEAD_NOTE` fires when "a new note or viewing is added to a lead". Whether API-created notes also fire it, or fire `UPDATE_LEADS`, is not stated. | Unverified | Fires **both** `ADD_LEAD_NOTE` and `UPDATE_LEADS` |
| 2 | Does `POST /reminder` fire a webhook or change `updateTime`/`reminderCount`? | `ADD_REMINDER` fires on any new reminder (documented). Effect on the lead is not stated. | Partly documented | Fires `ADD_REMINDER` **and** `UPDATE_LEADS`; bumps `reminderCount` and `updateTime` |
| 3 | Does a note change `updateTime`? | Not stated. | Unverified | Yes |
| 4 | Can a reminder be read, cancelled or replaced? | **Yes**: `POST /reminder/list`, `PUT /reminder`, `DELETE /reminder/{id}`. | Documented | Implemented; we only use list + create |
| 5 | Does `GET /lead/list` paginate and filter by `updateTime` or assignee? | It is a **POST**. It paginates (`page`, `size` ≤ 500 → `total`, `pages`, `current`) and **sorts** by `UPDATE_TIME`, but has **no** date-range or assignee filter. | Documented | Same |
| 6 | Can agent profiles and workload be read? | `GET /agent/list` exists, but its response schema is **undocumented**. Workload has no endpoint; derive it by counting leads per `agentId`. | Partly documented | Returns `[{id, name, email, phone, brn}]` |
| 7 | Do webhooks retry, arrive in order, or carry a signature? | **No signature** is documented. The body is the raw object **without the event name**, so use one callback URL per event. Retries and ordering are not stated. Receiver must reply 200. | Partly documented | Delivers once, in order, unsigned |
| 8 | Is there a sandbox? | None documented. | Documented (absent) | Mock is the test bed |
| 9 | Length or format limits on notes? | Not stated. `message` is a plain string. | Unverified | No limit |

## Other observations from the spec

- **No single-lead endpoint.** Fetch one lead with `lead/list` + `searchKeywords=<id>`.
- **Leads have no `propertyReference`.** The interested listing is a `property` object (often `null`). `propertyReference` exists only on portal leads and match results.
- **Ranges are pipe strings:** `budget "10000|100000"`, `rooms "Studio|6"`, `size "600|1500"`.
- **IDs are inconsistent:** `regionId` is a string in listings and an int in leads. `communityId` can be the string `"null"`. `bedRooms` is `-1` for multi-unit projects. The models normalise all of these.
- **Listing pages** return `totalSize` but no page count. Activity-log pages use `currentPage/totalSize/totalPages` instead of `current/total/pages`.
- **`AppName` path segment** on `addLog`/`update`: its effect is undocumented (probably shown as the source of the log). We send `PixxiAgents` (configurable).
- **`createdBy`** for API-written logs is undocumented; the mock uses user id `1`.
- **Missing-token behaviour** is undocumented; the mock returns 401.
- **`/agent/list`** and **`/reminder/list`** response shapes are undocumented; the mock uses the standard list envelope.
- The **Pixxi matching endpoint** returns `matchRate` (0–100) with per-field scores (BUDGET, LOCATION, ROOMS, ...). The mock's scoring is a simple stand-in, not Pixxi's algorithm.
- **Lead stage (`tagId`)** values are tenant-specific and undocumented. The seed dataset and mock assume 39 new, 40 contacted, 41 qualified, 42 dormant (unverified).
- Lead records carry `nationality`, `gender` and `birthdate`. These are excluded from scoring by `Lead.scoring_view()`.

## To verify on first real-API access

Run these against a **test account with synthetic leads only** and update the table above:
checks 1, 2, 3, 7 and 9; `AppName` display; `createdBy` value; timezone of timestamps; response shapes of
`/agent/list` and `/reminder/list`; whether `searchKeywords` matches IDs exactly.
