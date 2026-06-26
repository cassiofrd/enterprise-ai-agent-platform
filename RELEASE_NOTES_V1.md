# Release Notes — v1.0 / Project v6.2

## Summary

This release consolidates the supply chain multi-agent project into a production-style reference architecture.

## Validated capabilities

- Inventory Agent exposed through FastAPI
- Local execution with Uvicorn
- Deployment to Azure Container Apps
- Image build through Azure Container Registry Tasks
- Multi-provider LLM abstraction: OpenAI and Azure OpenAI
- Local FAISS RAG
- Optional Azure AI Search integration
- Long-term memory save and retrieval
- Memory persistence across process restarts
- Memory validation in Azure Container Apps
- Observability through JSONL logs and `/metrics`

## Important validated scenario

The following scenario was tested locally and in Azure:

1. User: `Registre que o fornecedor do PARAFUSO-M20 é XYZ Metais.`
2. Agent saves long-term memory.
3. User: `Qual o fornecedor do PARAFUSO-M20?`
4. Agent retrieves memory and answers: `XYZ Metais`.

## Known next steps

- Add automated pytest tests
- Add evaluation scripts
- Improve production mode response filtering
- Validate Supervisor + Supplier deployment after Inventory v6.2
- Continue Azure AI Foundry comparison
