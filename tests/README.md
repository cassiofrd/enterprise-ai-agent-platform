# Automated Tests

This folder contains the first automated quality gate for the project.

## Run

```cmd
pytest -q
```

## What is covered

- Long-term memory save/search/delete behavior.
- Product/SKU extraction for business identifiers such as `PARAFUSO-M20`.
- Portuguese memory-save trigger detection.
- Inventory Agent `/health` endpoint.
- End-to-end memory flow through `/invoke` using a deterministic LLM double.

The current tests avoid calling external LLM APIs. Functional LLM/RAG tests are covered by the evaluation pipeline in `evaluation/`.
