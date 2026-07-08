# Observability and Trace Explorer

This project records structured JSONL events for Supervisor, Inventory and Supplier agents.
The current observability layer supports:

- event logging to `logs/agent_events.jsonl`;
- in-memory and JSONL-backed metrics;
- trace indexes through `GET /traces`;
- per-trace details through `GET /traces/{trace_id}`;
- latency, token and cost summaries by trace;
- Streamlit Trace Explorer in the Observability page.

## Local endpoints

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/metrics" -Method GET
Invoke-RestMethod -Uri "http://localhost:8000/traces" -Method GET
Invoke-RestMethod -Uri "http://localhost:8000/traces/<TRACE_ID>" -Method GET
```

Inventory and Supplier expose the same endpoints on ports `8001` and `8002`.

## Streamlit

Run:

```powershell
streamlit run apps/streamlit_ui/app.py
```

Open the **Observability** page and use **Trace Explorer** to inspect:

- trace timeline;
- selected route;
- agent/tool events;
- observed latency per step;
- token and cost totals when available;
- raw events for debugging.

## Why this matters

This makes it easier to explain and debug the multi-agent flow:

```text
User → Foundry/Streamlit → Supervisor → Inventory/Supplier/Knowledge → Azure AI Search → LLM
```

The trace view is intentionally lightweight and local-first. A future production version can export the same events to Application Insights, Azure Monitor or OpenTelemetry.
