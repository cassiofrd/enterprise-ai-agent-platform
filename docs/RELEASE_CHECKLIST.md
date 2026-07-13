# Release Checklist — v1.0.0

Use this checklist before creating the GitHub release.

## Security

- [ ] `.env` is not committed.
- [ ] API keys are not present in README, docs or examples.
- [ ] Local database files are not committed.
- [ ] Runtime logs are not committed.
- [ ] Container App secrets are configured in Azure, not hardcoded.
- [ ] GitHub secret `AZURE_CREDENTIALS` is configured only in GitHub Actions secrets.

## Local validation

- [ ] `pytest -q` passes.
- [ ] Inventory starts on port `8001`.
- [ ] Supplier starts on port `8002`.
- [ ] Supervisor starts on port `8000`.
- [ ] `GET /health` works for all agents.
- [ ] `Quem fornece o PARAFUSO-M20?` returns XYZ Metais.
- [ ] `Qual é o risco dele?` resolves the prior supplier from session context.
- [ ] `O que a política diz sobre estoque crítico?` returns a RAG answer with sources.
- [ ] `GET /traces` returns trace summaries.
- [ ] `GET /traces/{trace_id}` returns event details.
- [ ] Streamlit app runs locally.
- [ ] Streamlit Observability page shows frontend history and Trace Explorer.

## Azure validation

- [ ] GitHub Actions Tests workflow passes.
- [ ] GitHub Actions Build and Deploy workflow passes.
- [ ] Azure Container Apps revisions are running.
- [ ] Inventory `/data-source-status` shows Azure AI Search when configured.
- [ ] Published Inventory `/products/PARAFUSO-M20` returns `source: azure_ai_search`.
- [ ] Supervisor published endpoint responds to `/copilot`.
- [ ] Azure AI Search index `supply-chain-docs` exists.
- [ ] Document ingestion has been executed after index creation.

## Documentation

- [ ] README updated.
- [ ] Architecture docs updated.
- [ ] Observability docs updated.
- [ ] Production readiness docs updated.
- [ ] Release notes updated.
- [ ] Version file set to `1.0.0`.

## GitHub release

Suggested tag:

```text
v1.0.0
```

Suggested title:

```text
Enterprise AI Agent Platform v1.0.0
```

Suggested release description:

```text
First stable release of the enterprise multi-agent supply-chain copilot platform with Azure AI Foundry integration, Azure Container Apps deployment, Azure AI Search RAG, contextual memory, source citations, trace observability and CI/CD.
```


## Final production checklist
- Configure Azure Application Insights.
- Configure Container Apps liveness/readiness probes (/live,/ready).
- Keep only supervisor externally exposed.
