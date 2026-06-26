# Release Checklist

Use this checklist before pushing a new version to GitHub.

## Security

- [ ] `.env` is not committed.
- [ ] API keys are not present in README or docs.
- [ ] Container App secrets are configured in Azure, not hardcoded.

## Local validation

- [ ] `uvicorn apps.inventory_agent.main:app --reload --port 8001` starts successfully.
- [ ] `GET /health` returns `status: ok`.
- [ ] RAG question works.
- [ ] Memory save works.
- [ ] Memory retrieval works.
- [ ] Memory still works after restarting Uvicorn.

## Azure validation

- [ ] ACR build finishes successfully.
- [ ] Image tag exists in ACR.
- [ ] Container App update succeeds.
- [ ] Azure `/health` works.
- [ ] Azure `/invoke` works.
- [ ] Long-term memory save/retrieve works in Azure.

## Documentation

- [ ] README updated.
- [ ] Architecture docs updated.
- [ ] API examples updated.
- [ ] Azure deployment guide updated.
- [ ] Provider configuration documented.

## Future quality gate

- [ ] Add pytest suite.
- [ ] Add evaluation script.
- [ ] Add CI workflow.
