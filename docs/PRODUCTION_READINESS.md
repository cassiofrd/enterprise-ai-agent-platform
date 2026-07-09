# Production Readiness

## v1.0 status

The v1.0 platform is ready as a reference implementation and portfolio-grade enterprise AI architecture.

Implemented:

- Multi-agent Supervisor + Inventory + Supplier architecture.
- FastAPI services with OpenAPI-compatible endpoints.
- Azure Container Apps deployment through GitHub Actions.
- Azure AI Search for structured data and document RAG.
- Azure AI Foundry integration through OpenAPI tools.
- Contextual memory using `session_id`.
- Conversation audit endpoints.
- Source citations for RAG answers.
- Trace IDs, `/metrics`, `/traces` and `/traces/{trace_id}`.
- Streamlit chat and Trace Explorer dashboard.
- Automated pytest suite.

## Current production gaps

These are intentionally left for the next phase because they depend on enterprise identity, operations and governance choices.

| Area | Current v1.0 | Recommended v2 |
|---|---|---|
| Secrets | Container App secrets / local `.env` | Azure Key Vault + Managed Identity |
| Authentication | Public/local endpoints | Microsoft Entra ID + RBAC |
| Observability | JSONL + Trace Explorer | Application Insights / Azure Monitor / OpenTelemetry |
| Memory | Local/session storage | Redis or managed persistent store |
| Cache | Local utility layer | Redis cache with TTL and hit-rate dashboards |
| Evaluation | Manual + tests | Azure AI Foundry evaluations / regression suite |
| Network | Public endpoints for demo | Private endpoints / VNet integration where needed |
| Release management | GitHub Actions | Tagged releases, environments and approvals |

## Recommended v2 roadmap

1. **Application Insights / OpenTelemetry**
   - Export trace events, latency and error metrics.
   - Correlate frontend, Supervisor and specialist agent spans.

2. **Redis cache and memory**
   - Cache repeated RAG and structured responses.
   - Store shared session state across replicas.

3. **Microsoft Entra ID and RBAC**
   - Protect endpoints.
   - Add role-based access for users and tools.

4. **Key Vault + Managed Identity**
   - Remove direct secret management from Container Apps.
   - Use managed identity for Azure AI Search and dependent services when available.

5. **Evaluation pipeline**
   - Build a dataset of expected answers.
   - Measure groundedness, correctness, latency and cost.

6. **Teams / Microsoft 365 Copilot channel**
   - Replace Streamlit as the primary user channel.
   - Keep Streamlit for demos and observability.

## Final v1.0 validation commands

```powershell
$env:PYTHONPATH = (Get-Location).Path
pytest -q
```

```powershell
$body = @{ question = "Quem fornece o PARAFUSO-M20?"; session_id = "release-001" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/copilot" -Method POST -ContentType "application/json; charset=utf-8" -Body $body
```

```powershell
$body = @{ question = "Qual é o risco dele?"; session_id = "release-001" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/copilot" -Method POST -ContentType "application/json; charset=utf-8" -Body $body
```

```powershell
$body = @{ question = "O que a política diz sobre estoque crítico?"; session_id = "release-rag-001" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/copilot" -Method POST -ContentType "application/json; charset=utf-8" -Body $body
```

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/traces" -Method GET
```
