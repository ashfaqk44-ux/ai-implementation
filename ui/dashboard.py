"""Live dashboard: watch new leads, the AI notes agents write, and what the receiver decided.

Run (with the mock, receiver and subscriptions running; see CHAT_HISTORY.md section 5):
    uv run streamlit run ui/dashboard.py        ->  http://localhost:8501

Read-only towards Pixxi. The sidebar buttons only call the mock's /mock/* endpoints, which
simulate a new lead arriving or a real estate agent logging a note; the middleware then reacts
exactly as it would to real Pixxi webhooks.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # `streamlit run` only adds ui/

import streamlit as st  # noqa: E402

from src.pixxi.read_tools import PixxiError, ReadTools  # noqa: E402
from ui.data import (  # noqa: E402
    MIN_REFRESH_SECONDS,
    MockControl,
    count_ai_notes_written,
    dashboard_settings,
    fetch_leads,
    has_layer1_note,
    lead_notes,
    lead_row,
    llm_usage,
    load_arrivals,
    new_in_last,
    next_arrival,
    read_jsonl_tail,
    read_sqlite_rows,
    service_up,
)

STATUS_LEADS = 15  # in mock mode, check the Layer 1 note status of the newest N leads

st.set_page_config(page_title="Pixxi Agents: live view", page_icon="🏠", layout="wide")


@st.cache_resource
def resources():
    settings = dashboard_settings()
    return settings, ReadTools(settings), MockControl(settings)


settings, tools, mock = resources()
is_mock = settings.pixxi_mode == "mock"
runs_dir = settings.eval_runs_dir

# -- sidebar ---------------------------------------------------------------------------------
with st.sidebar:
    st.header("Connection")
    pixxi_ok = service_up(settings.pixxi_base_url.rstrip("/") + "/docs")
    receiver_ok = service_up(settings.webhook_public_base_url.rstrip("/") + "/health")
    st.write(f"{'🟢' if pixxi_ok else '🔴'} Pixxi ({settings.pixxi_mode}) · `{settings.pixxi_base_url}`")
    st.write(f"{'🟢' if receiver_ok else '🔴'} Receiver + orchestrator · `{settings.webhook_public_base_url}`")
    if not receiver_ok:
        st.caption("Start it with `uv run uvicorn src.main:create_app --factory --port 8002`, "
                   "then `uv run python -m scripts.subscribe_webhooks`.")

    floor = MIN_REFRESH_SECONDS["mock" if is_mock else "real"]
    refresh = st.slider("Refresh every (seconds)", floor, 60, max(3, floor))

    st.header("Simulate")
    if not is_mock:
        st.info("Simulation is only available against the mock.")
    else:
        st.caption(f"Each new lead makes Agent 1 call the LLM once (≈ $0.0008 with {settings.llm_model}).")
        if st.button("➕ New lead arrives", width="stretch", disabled=not pixxi_ok):
            try:
                existing, _ = fetch_leads(tools, size=200)
                created = mock.new_lead(next_arrival(load_arrivals(), existing))
                st.session_state["selected_lead"] = created["id"]
                st.toast(f"Lead {created['id']} arrived ({created.get('clientSource')}). "
                         "Agent 1's note appears in a few seconds.")
            except Exception as exc:
                st.error(f"Could not create lead: {exc}")

        with st.form("human_note", clear_on_submit=True):
            st.write("📝 Real estate agent logs a note")
            note_lead = st.number_input("Lead id", value=int(st.session_state.get("selected_lead") or 0), step=1)
            note_text = st.text_area("Note", placeholder="Called client: budget firm at 1.5M, wants handover in 2027, mortgage pre-approved.")
            if st.form_submit_button("Log note", disabled=not pixxi_ok) and note_text.strip():
                try:
                    mock.human_note(int(note_lead), note_text.strip())
                    st.toast(f"Note logged on lead {int(note_lead)}.")
                except Exception as exc:
                    st.error(f"Could not log note: {exc}")

        if st.button("↺ Reset mock data", width="stretch", disabled=not pixxi_ok):
            mock.reset()
            st.session_state.pop("selected_lead", None)
            st.toast("Mock reset. Run `scripts.subscribe_webhooks` again to reconnect the receiver.")

st.title("🏠 Pixxi Agents: live view")


# -- metrics + leads + detail (auto-refreshing) -------------------------------------------
@st.fragment(run_every=refresh)
def live_leads() -> None:
    if not pixxi_ok:
        st.error("Pixxi (mock) is not reachable. Start it with "
                 "`$env:MOCK_FIXTURES_DIR=\"data/synthetic/seed\"; uv run uvicorn mock_pixxi.app:app --port 8001`.")
        return
    try:
        leads, total = fetch_leads(tools)
    except PixxiError as exc:
        st.error(f"Could not read leads: {exc}")
        return

    notes_cache: dict[int, list] = {}
    status: dict[int, bool | None] = {}
    for i, lead in enumerate(leads):
        if is_mock and i < STATUS_LEADS:  # the mock is local; on the real API this would cost reads
            notes_cache[lead.id] = lead_notes(tools, lead.id)
            status[lead.id] = has_layer1_note(notes_cache[lead.id])
        else:
            status[lead.id] = None

    usage = llm_usage(runs_dir / "llm_calls.jsonl")
    c = st.columns(5)
    c[0].metric("Leads in Pixxi", total if total is not None else len(leads))
    c[1].metric("New in last 24 h", new_in_last(leads))
    c[2].metric("AI notes written", count_ai_notes_written(settings.state_db), help="All time, from the write ledger")
    c[3].metric("LLM calls", usage["calls"], help=f"{usage['input_tokens']} in / {usage['output_tokens']} out tokens"
                                                  f"{', ' + str(usage['errors']) + ' failed' if usage['errors'] else ''}")
    c[4].metric("LLM spend (est.)", f"${usage['cost_usd']:.4f}",
                help=f"{usage['last_model'] or settings.llm_model} · {usage['last_prompt'] or '—'}")

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader("Leads (newest first)")
        st.dataframe([lead_row(l, status[l.id]) for l in leads], hide_index=True, width="stretch",
                     height=min(38 * (len(leads) + 1), 560))
        st.caption("✅ Agent 1 wrote a Layer 1 note · ⏳ not yet" + (f" · status shown for the newest {STATUS_LEADS}" if is_mock else ""))

    with right:
        st.subheader("Lead detail")
        ids = [l.id for l in leads]
        if not ids:
            st.info("No leads yet. Click **New lead arrives** in the sidebar.")
            return
        wanted = st.session_state.get("selected_lead")
        index = ids.index(wanted) if wanted in ids else 0
        lead_id = st.selectbox("Lead", ids, index=index,
                               format_func=lambda i: f"{i} · {next(l for l in leads if l.id == i).clientSource}")
        st.session_state["selected_lead"] = lead_id
        lead = next(l for l in leads if l.id == lead_id)
        row = lead_row(lead, status.get(lead_id))
        st.markdown(f"**{row['type']} · {row['source']}** · budget AED {row['budget (AED)']} · "
                    f"{row['bedrooms']} BR · {row['region'] or '—'}  \n"
                    f"Enquiry: {(lead.interested_property or {}).get('title') or '—'}  \n"
                    f"_{lead.notes or ''}_")

        notes = notes_cache.get(lead_id) or lead_notes(tools, lead_id)
        if not notes:
            st.info("No notes yet. If this lead just arrived, Agent 1 is working on it…")
        for n in notes:
            when = n.created.strftime("%Y-%m-%d %H:%M") if n.created else ""
            if n.ai is not None:
                with st.container(border=True):
                    st.markdown(f"🤖 **{n.ai.agent_id}** · {when}")
                    st.markdown(f"**{n.ai.summary}**")
                    st.write(n.ai.rationale)
                    if n.ai.action_items:
                        st.markdown("**Please confirm with the client:**\n" +
                                    "\n".join(f"- {q}" for q in n.ai.action_items))
                    chips = [f"`{k}: {v}`" for k, v in n.ai.decision.items() if k != "layer"]
                    check = (n.ai.input_signals or {}).get("listing_check", {}).get("result")
                    if check:
                        chips.append(f"`listing_check: {check}`")
                    st.markdown(" ".join(chips))
                    with st.expander("Audit record (JSON stored in Pixxi)"):
                        st.json(n.ai.model_dump(mode="json", by_alias=True))
            else:
                with st.container(border=True):
                    st.markdown(f"👤 **Human note** (user {n.created_by}) · {when}")
                    st.write(n.human_text)


# -- activity feed (auto-refreshing) ----------------------------------------------------------
@st.fragment(run_every=refresh)
def activity_feed() -> None:
    st.subheader("What the middleware did")
    hooks_col, runs_col = st.columns(2, gap="large")
    with hooks_col:
        st.markdown("**Webhooks received** (newest first)")
        hooks = read_sqlite_rows(settings.state_db, "webhook_log", limit=40)
        if not hooks:
            st.caption("Nothing yet. Is the receiver running and subscribed?")
        else:
            st.dataframe([{"time": h["received_at"][11:19], "event": h["event"], "object": h["object_id"],
                           "decision": ("✅ " if h["decision"] == "accepted" else "🚫 ") + h["decision"],
                           "reason": h["reason"]} for h in hooks],
                         hide_index=True, width="stretch", height=360)
            st.caption("🚫 ignored = the system's own writes (loop guard) or duplicates.")
    with runs_col:
        st.markdown("**Orchestrator runs** (newest first)")
        runs = read_jsonl_tail(runs_dir / "orchestrator_runs.jsonl", limit=40)
        if not runs:
            st.caption("No runs yet.")
        else:
            st.dataframe([{"time": (r.get("timestamp") or "")[11:19], "event": r.get("event"), "lead": r.get("lead_id"),
                           "route": " → ".join(r.get("route") or []) or "—", "outcome": r.get("outcome"),
                           "notes": r.get("notes_posted"), "detail": r.get("detail")} for r in runs],
                         hide_index=True, width="stretch", height=360)
            st.caption("Times in UTC. Step 4 only routes ADD_LEADS → Agent 1; other events show no_route.")


live_leads()
st.divider()
activity_feed()
