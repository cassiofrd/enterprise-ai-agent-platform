# Release Notes — v1.0.0

## Summary

This release consolidates the project as an enterprise-style multi-agent AI platform for supply-chain scenarios.

The platform demonstrates how Azure AI Foundry, Azure Container Apps, Azure AI Search, FastAPI, RAG, contextual memory, OpenAPI tools, CI/CD and observability can work together in a production-oriented architecture.

## Included capabilities

### Multi-agent platform

- Supervisor Agent.
- Inventory Agent.
- Supplier Agent.
- Direct Supervisor routing and Azure AI Foundry OpenAPI tool mode.
- Structured responses for product and supplier questions.

### Azure AI Search

- Structured entity lookup for products, suppliers and policies.
- Document ingestion for supply-chain knowledge.
- RAG answer generation with source citations.

### Context memory and audit

- `session_id` support.
- Follow-up resolution such as “Qual é o risco dele?”.
- Conversation audit endpoint.
- Session context persistence for key product and supplier entities.

### Observability

- `trace_id` propagation.
- JSONL event logging.
- `/metrics`, `/traces` and `/traces/{trace_id}` endpoints.
- Streamlit Observability page.
- Trace Explorer timeline and frontend execution history.

### Deployment and DevOps

- Dockerfiles for Inventory, Supplier and Supervisor.
- Azure Container Apps deployment workflow.
- GitHub Actions tests workflow.
- OpenAPI schemas for Azure AI Foundry tools.

### Test coverage

- Health endpoints.
- Inventory entity extraction.
- Inventory and Supplier OpenAPI tool endpoints.
- Memory store and contextual memory.
- Observability trace reconstruction.

## Validated scenarios

1. Product supplier lookup:

```text
User: Quem fornece o PARAFUSO-M20?
Agent: O produto PARAFUSO-M20 é fornecido por XYZ Metais.
```

2. Contextual follow-up:

```text
User: Qual é o risco dele?
Agent: O fornecedor XYZ Metais tem risco low e rating A.
```

3. RAG with sources:

```text
User: O que a política diz sobre estoque crítico?
Agent: Answer grounded in inventory_policy_critical_stock.md with source citation.
```

4. Trace inspection:

```text
GET /traces/{trace_id}
```

returns a reconstructed multi-agent execution summary and event timeline.

## Known limitations

- Observability is local-first JSONL, not yet exported to Application Insights or OpenTelemetry.
- Memory is suitable for demos and local/container execution, but Redis or a managed store is recommended for distributed production scale.
- Authentication and authorization are not part of v1.0.
- Secrets are expected to be managed outside the repository through `.env`, Container App secrets or future Key Vault integration.

## Recommended next release

v2 should focus on production hardening:

- Application Insights / OpenTelemetry.
- Redis cache and distributed memory.
- Microsoft Entra ID + RBAC.
- Key Vault + Managed Identity.
- Automated Azure AI Foundry evaluations.
- Teams / Microsoft 365 Copilot publication.
