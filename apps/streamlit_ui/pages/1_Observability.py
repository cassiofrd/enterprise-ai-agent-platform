from __future__ import annotations

import requests
import pandas as pd
import streamlit as st


SUPERVISOR_METRICS_URL = (
    "https://supervisor-agent.politedune-38af7eb9.brazilsouth.azurecontainerapps.io/metrics"
)

INVENTORY_METRICS_URL = (
    "https://inventory-agent.politedune-38af7eb9.brazilsouth.azurecontainerapps.io/metrics"
)

SUPPLIER_METRICS_URL = (
    "https://supplier-agent.politedune-38af7eb9.brazilsouth.azurecontainerapps.io/metrics"
)

INVENTORY_MEMORIES_URL = (
    "https://inventory-agent.politedune-38af7eb9.brazilsouth.azurecontainerapps.io/memories"
)

SUPPLIER_MEMORIES_URL = (
    "https://supplier-agent.politedune-38af7eb9.brazilsouth.azurecontainerapps.io/memories"
)

METRIC_URLS = {
    "supervisor": SUPERVISOR_METRICS_URL,
    "inventory": INVENTORY_METRICS_URL,
    "supplier": SUPPLIER_METRICS_URL,
}

MEMORY_URLS = {
    "inventory": INVENTORY_MEMORIES_URL,
    "supplier": SUPPLIER_MEMORIES_URL,
}


st.set_page_config(
    page_title="Supply Chain Observability",
    page_icon="📊",
    layout="wide",
)

st.title("📊 Supply Chain Multi-Agent Observability")
st.caption(
    "Metrics, traces, memory and cost monitoring for Supervisor, Inventory and Supplier agents."
)


def fetch_json(url: str) -> dict:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def safe_fetch(name: str, url: str) -> dict:
    try:
        return fetch_json(url)
    except Exception as exc:
        st.warning(f"Failed to load {name}: {exc}")
        return {"agent": name, "summary": {}, "events": []}


def delete_memory(agent: str, memory_id: str) -> dict:
    base_url = MEMORY_URLS[agent]
    response = requests.delete(f"{base_url}/{memory_id}", timeout=30)
    response.raise_for_status()
    return response.json()


def load_memories(agent: str, url: str) -> list[dict]:
    try:
        payload = fetch_json(url)
        rows = payload.get("memories", []) or []
        return [dict(row, memory_agent=agent) for row in rows]
    except Exception as exc:
        st.warning(f"Failed to load {agent} memories: {exc}")
        return []


def build_events(payloads: dict[str, dict]) -> pd.DataFrame:
    events: list[dict] = []
    for source_name, payload in payloads.items():
        for event in payload.get("events", []):
            item = dict(event)
            item["source_agent"] = source_name
            events.append(item)

    if not events:
        return pd.DataFrame()

    df = pd.DataFrame(events)
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp", ascending=False)
    return df


def contains_any_agent(row: pd.Series, selected_agents: list[str]) -> bool:
    haystack = " ".join(
        str(row.get(col, ""))
        for col in ["source_agent", "agent", "target", "target_agent", "route", "tool", "event_type"]
    ).lower()
    return any(agent.lower() in haystack for agent in selected_agents)


payloads = {
    agent: safe_fetch(agent, url)
    for agent, url in METRIC_URLS.items()
}

summaries = {
    agent: payload.get("summary", {}) or {}
    for agent, payload in payloads.items()
}

summary_rows = []
for agent_name, summary in summaries.items():
    summary_rows.append(
        {
            "agent": agent_name,
            "events": summary.get("total_events", 0),
            "traces": summary.get("total_traces", 0),
            "tokens": summary.get("total_tokens", 0),
            "estimated_cost_usd": summary.get("estimated_total_cost_usd", 0),
            "avg_latency_ms": summary.get("avg_latency_ms"),
            "cache_hits": summary.get("cache_hits", 0),
            "cache_misses": summary.get("cache_misses", 0),
        }
    )

summary_df = pd.DataFrame(summary_rows)

all_events_df = build_events(payloads)

st.sidebar.header("Dashboard filters")
available_agents = ["supervisor", "inventory", "supplier", "validator"]
selected_agents = st.sidebar.multiselect(
    "Agents",
    available_agents,
    default=available_agents,
)

if not selected_agents:
    selected_agents = available_agents

if not all_events_df.empty:
    filtered_events_df = all_events_df[
        all_events_df.apply(lambda row: contains_any_agent(row, selected_agents), axis=1)
    ].copy()
else:
    filtered_events_df = pd.DataFrame()

filtered_summary_df = summary_df[summary_df["agent"].isin(selected_agents)].copy()
if filtered_summary_df.empty:
    filtered_summary_df = summary_df.copy()


total_events = int(filtered_summary_df["events"].fillna(0).sum())
total_traces = int(filtered_summary_df["traces"].fillna(0).sum())
total_tokens = int(filtered_summary_df["tokens"].fillna(0).sum())
total_cost = float(filtered_summary_df["estimated_cost_usd"].fillna(0).sum())

cache_hits = int(filtered_summary_df["cache_hits"].fillna(0).sum())
cache_misses = int(filtered_summary_df["cache_misses"].fillna(0).sum())
cache_total = cache_hits + cache_misses
cache_hit_rate = round((cache_hits / cache_total) * 100, 2) if cache_total else 0
cache_savings = cache_hits

latencies = filtered_summary_df["avg_latency_ms"].dropna().tolist()
avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0

most_expensive_agent = (
    filtered_summary_df.sort_values("estimated_cost_usd", ascending=False).iloc[0]["agent"]
    if not filtered_summary_df.empty
    else "n/a"
)

latency_rank = filtered_summary_df.dropna(subset=["avg_latency_ms"])
slowest_agent = (
    latency_rank.sort_values("avg_latency_ms", ascending=False).iloc[0]["agent"]
    if not latency_rank.empty
    else "n/a"
)


st.subheader("General KPIs")
col1, col2, col3 = st.columns(3)
col1.metric("Total events", total_events)
col2.metric("Total traces", total_traces)
col3.metric("Total tokens", total_tokens)

col4, col5, col6 = st.columns(3)
col4.metric("Estimated cost", f"${total_cost:.6f}")
col5.metric("Cache hit rate", f"{cache_hit_rate:.2f}%")
col6.metric("Avg latency", f"{avg_latency:.0f} ms")

col7, col8, col9 = st.columns(3)
col7.metric("Cache savings", cache_savings)
col8.metric("Most expensive agent", most_expensive_agent)
col9.metric("Slowest agent", slowest_agent)


st.subheader("Agent summaries")
st.dataframe(filtered_summary_df, use_container_width=True)

chart_col_1, chart_col_2, chart_col_3 = st.columns(3)
with chart_col_1:
    st.markdown("**Cost by agent**")
    st.bar_chart(filtered_summary_df[["agent", "estimated_cost_usd"]], x="agent", y="estimated_cost_usd")
with chart_col_2:
    st.markdown("**Tokens by agent**")
    st.bar_chart(filtered_summary_df[["agent", "tokens"]], x="agent", y="tokens")
with chart_col_3:
    st.markdown("**Average latency by agent**")
    st.bar_chart(filtered_summary_df[["agent", "avg_latency_ms"]], x="agent", y="avg_latency_ms")

st.subheader("Cache")
cache_df = pd.DataFrame(
    [
        {"type": "hits", "count": cache_hits},
        {"type": "misses", "count": cache_misses},
    ]
)
st.bar_chart(cache_df, x="type", y="count")


st.subheader("🧠 Long-Term Memory")
all_memories: list[dict] = []
for agent, url in MEMORY_URLS.items():
    all_memories.extend(load_memories(agent, url))

if all_memories:
    memory_df = pd.DataFrame(all_memories)

    mem_col_1, mem_col_2, mem_col_3, mem_col_4 = st.columns(4)
    mem_col_1.metric("Stored memories", len(memory_df))
    mem_col_2.metric("Memory agents", memory_df["memory_agent"].nunique() if "memory_agent" in memory_df.columns else 0)
    mem_col_3.metric("Source agents", memory_df["source_agent"].nunique() if "source_agent" in memory_df.columns else 0)
    mem_col_4.metric("Memory types", memory_df["memory_type"].nunique() if "memory_type" in memory_df.columns else 0)

    memory_agents = sorted(memory_df["memory_agent"].dropna().unique().tolist())
    selected_memory_agents = st.multiselect(
        "Filter memory agent",
        memory_agents,
        default=memory_agents,
    )

    search_term = st.text_input(
        "Search memories",
        placeholder="Example: SKU-2002, supplier, Northwind, hazardous",
    )

    filtered_memory_df = memory_df.copy()

    if selected_memory_agents:
        filtered_memory_df = filtered_memory_df[
            filtered_memory_df["memory_agent"].isin(selected_memory_agents)
        ]

    if search_term:
        search_lower = search_term.lower()
        searchable_cols = [
            col
            for col in ["key", "value", "memory_type", "source_agent", "memory_agent"]
            if col in filtered_memory_df.columns
        ]

        if searchable_cols:
            mask = pd.Series(False, index=filtered_memory_df.index)
            for col in searchable_cols:
                mask = mask | filtered_memory_df[col].astype(str).str.lower().str.contains(
                    search_lower,
                    na=False,
                    regex=False,
                )
            filtered_memory_df = filtered_memory_df[mask]

    preferred_memory_cols = [
        "memory_agent",
        "created_at",
        "memory_type",
        "key",
        "value",
        "source_agent",
        "id",
    ]
    visible_memory_cols = [
        col for col in preferred_memory_cols if col in filtered_memory_df.columns
    ]

    st.dataframe(
        filtered_memory_df[visible_memory_cols],
        use_container_width=True,
        height=320,
    )

    st.markdown("**Delete memory**")

    if filtered_memory_df.empty:
        st.info("No memories match the current filter.")
    else:
        delete_options = []
        for _, row in filtered_memory_df.iterrows():
            memory_agent = str(row.get("memory_agent", ""))
            memory_id = str(row.get("id", ""))
            key = str(row.get("key", ""))
            value = str(row.get("value", ""))
            value_preview = value[:80] + "..." if len(value) > 80 else value
            label = f"{memory_agent} | {key} | {value_preview} | {memory_id}"
            delete_options.append({"label": label, "id": memory_id, "agent": memory_agent})

        selected_memory_label = st.selectbox(
            "Select memory to delete",
            [option["label"] for option in delete_options],
        )

        selected_option = next(
            option for option in delete_options if option["label"] == selected_memory_label
        )

        confirm_delete = st.checkbox(
            "I understand this will delete the selected memory.",
            key="confirm_delete_memory",
        )

        if st.button("Delete selected memory", disabled=not confirm_delete, type="primary"):
            try:
                result = delete_memory(selected_option["agent"], selected_option["id"])
                if result.get("deleted"):
                    st.success(
                        f"Deleted {selected_option['agent']} memory: {selected_option['id']}"
                    )
                    st.rerun()
                else:
                    st.warning(f"Memory was not deleted: {selected_option['id']}")
            except Exception as exc:
                st.error(f"Failed to delete memory: {exc}")

    with st.expander("Raw memories"):
        st.json(all_memories)
else:
    st.info("No long-term memories found yet. Ask an agent to remember something and refresh this page.")


if filtered_events_df.empty:
    st.info("No events found for the selected filters. Send a question to the agent and refresh this page.")
    st.stop()

st.subheader("Routing overview")
if "route" in filtered_events_df.columns:
    route_df = filtered_events_df[filtered_events_df["route"].notna()].copy()
    if not route_df.empty:
        route_counts = route_df["route"].astype(str).value_counts().reset_index()
        route_counts.columns = ["route", "count"]
        st.bar_chart(route_counts, x="route", y="count")
        st.dataframe(route_counts, use_container_width=True)
    else:
        st.info("No route events found yet.")
else:
    st.info("No route column found yet.")

st.subheader("Top Event Types")
event_type_counts = filtered_events_df["event_type"].value_counts().reset_index()
event_type_counts.columns = ["event_type", "count"]
st.bar_chart(event_type_counts, x="event_type", y="count")
st.dataframe(event_type_counts, use_container_width=True, height=300)

st.subheader("Cost Breakdown")
cost_events_df = filtered_events_df[
    filtered_events_df.get("estimated_total_cost_usd", pd.Series(dtype=float)).notna()
].copy()

if not cost_events_df.empty:
    cost_by_agent = (
        cost_events_df.groupby("agent", dropna=False)["estimated_total_cost_usd"]
        .sum()
        .reset_index()
        .sort_values("estimated_total_cost_usd", ascending=False)
    )

    cost_by_event_type = (
        cost_events_df.groupby("event_type", dropna=False)["estimated_total_cost_usd"]
        .sum()
        .reset_index()
        .sort_values("estimated_total_cost_usd", ascending=False)
    )

    col_cost_1, col_cost_2 = st.columns(2)
    with col_cost_1:
        st.markdown("**Cost by agent**")
        st.bar_chart(cost_by_agent, x="agent", y="estimated_total_cost_usd")
        st.dataframe(cost_by_agent, use_container_width=True)
    with col_cost_2:
        st.markdown("**Cost by event type**")
        st.bar_chart(cost_by_event_type, x="event_type", y="estimated_total_cost_usd")
        st.dataframe(cost_by_event_type, use_container_width=True)
else:
    st.info("No cost events found yet.")

st.subheader("Agent Analytics")
analytics_rows = []
for agent_name in available_agents:
    agent_events = filtered_events_df[
        (filtered_events_df.get("agent") == agent_name)
        | (filtered_events_df.get("source_agent") == agent_name)
    ]

    if agent_events.empty:
        continue

    request_events = agent_events[
        agent_events["event_type"].astype(str).str.contains(
            "request|chat|invoke",
            case=False,
            regex=True,
        )
    ]

    usage_events = agent_events[
        agent_events["event_type"].astype(str).str.contains(
            "usage",
            case=False,
            regex=False,
        )
    ]

    latency_events = agent_events[
        agent_events.get("latency_ms", pd.Series(dtype=float)).notna()
    ]

    analytics_rows.append(
        {
            "agent": agent_name,
            "events": len(agent_events),
            "request_like_events": len(request_events),
            "usage_events": len(usage_events),
            "tokens": usage_events.get("total_tokens", pd.Series(dtype=float)).fillna(0).sum(),
            "estimated_cost_usd": usage_events.get("estimated_total_cost_usd", pd.Series(dtype=float)).fillna(0).sum(),
            "avg_latency_ms": round(latency_events["latency_ms"].mean(), 2)
            if not latency_events.empty
            else None,
            "max_latency_ms": round(latency_events["latency_ms"].max(), 2)
            if not latency_events.empty
            else None,
        }
    )

if analytics_rows:
    analytics_df = pd.DataFrame(analytics_rows)
    st.dataframe(analytics_df, use_container_width=True)

    col_analytics_1, col_analytics_2 = st.columns(2)
    with col_analytics_1:
        st.markdown("**Average latency by agent**")
        st.bar_chart(analytics_df, x="agent", y="avg_latency_ms")
    with col_analytics_2:
        st.markdown("**Estimated cost by agent**")
        st.bar_chart(analytics_df, x="agent", y="estimated_cost_usd")
else:
    st.info("No agent analytics available yet.")

st.subheader("Trace Explorer")
trace_ids = sorted(
    [
        trace_id
        for trace_id in filtered_events_df.get("trace_id", pd.Series(dtype=str)).dropna().unique()
    ],
    reverse=True,
)

if trace_ids:
    selected_trace_id = st.selectbox("Select a trace_id", trace_ids)

    trace_df = filtered_events_df[filtered_events_df["trace_id"] == selected_trace_id].copy()
    trace_df = trace_df.sort_values("timestamp", ascending=True)

    trace_cost = trace_df["estimated_total_cost_usd"].fillna(0).sum() if "estimated_total_cost_usd" in trace_df.columns else 0
    trace_tokens = trace_df["total_tokens"].fillna(0).sum() if "total_tokens" in trace_df.columns else 0
    trace_latency = trace_df["latency_ms"].fillna(0).sum() if "latency_ms" in trace_df.columns else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trace events", len(trace_df))
    c2.metric("Trace tokens", int(trace_tokens))
    c3.metric("Trace cost", f"${trace_cost:.6f}")
    c4.metric("Observed latency", f"{trace_latency:.0f} ms")

    st.markdown("**Execution timeline**")
    timeline_cols = [
        "timestamp",
        "source_agent",
        "event_type",
        "agent",
        "status",
        "latency_ms",
        "tool",
        "route",
        "target",
        "target_agent",
        "total_tokens",
        "estimated_total_cost_usd",
    ]
    visible_timeline_cols = [col for col in timeline_cols if col in trace_df.columns]
    st.dataframe(trace_df[visible_timeline_cols], use_container_width=True, height=420)

    timeline_labels = []
    for _, row in trace_df.iterrows():
        event_type = str(row.get("event_type", ""))
        source_agent = str(row.get("source_agent", ""))
        route = row.get("route")
        tool = row.get("tool")
        latency = row.get("latency_ms")

        extras = []
        if pd.notna(route):
            extras.append(f"route={route}")
        if pd.notna(tool):
            extras.append(f"tool={tool}")
        if pd.notna(latency):
            extras.append(f"latency={round(float(latency), 2)}ms")

        extra_text = f" ({', '.join(extras)})" if extras else ""
        timeline_labels.append(f"{source_agent} → {event_type}{extra_text}")

    with st.expander("Readable execution timeline"):
        for index, label in enumerate(timeline_labels, start=1):
            st.write(f"{index}. {label}")

    with st.expander("Raw trace events"):
        st.json(trace_df.to_dict(orient="records"))
else:
    st.info("No trace_id found yet.")

st.subheader("Recent events")
preferred_cols = [
    "timestamp",
    "source_agent",
    "event_type",
    "trace_id",
    "agent",
    "status",
    "latency_ms",
    "tool",
    "route",
    "target",
    "target_agent",
    "total_tokens",
    "estimated_total_cost_usd",
]
visible_cols = [col for col in preferred_cols if col in filtered_events_df.columns]
st.dataframe(filtered_events_df[visible_cols], use_container_width=True, height=500)
