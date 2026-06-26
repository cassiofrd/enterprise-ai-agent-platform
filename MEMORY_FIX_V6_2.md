# Memory Fix v6.2

This version improves long-term memory retrieval for product-specific supplier facts.

## What changed

- `save_agent_memory` now normalizes weak LLM-generated keys.
- Supplier memories are stored with structured keys such as `inventory:supplier:PARAFUSO-M20`.
- `search_agent_memory` expands queries using product/entity extraction before searching.
- Logs now include raw and normalized memory keys.

## Regression test

1. `Registre que o fornecedor do PARAFUSO-M20 é XYZ Metais.`
2. `Qual o fornecedor do PARAFUSO-M20?`

Expected answer: `XYZ Metais`.
