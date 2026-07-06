# Azure AI Search integration

This project can run with local reference data or with Azure AI Search as the structured knowledge layer.

## Current behavior

The REST endpoints remain deterministic:

- `GET /products/{code}`
- `GET /inventory-policy/{code}`
- `GET /suppliers/{supplier_name}`
- `GET /suppliers/{supplier_name}/contracts`
- `GET /suppliers/{supplier_name}/performance`

When Azure AI Search is configured, Inventory and Supplier first try to read structured entities from the Search index. If Search is not configured, unavailable, or the entity is not present, the agents fall back to the local demo reference data.

This keeps local development and CI simple while allowing the same API contract to use Azure AI Search in cloud environments.

## Environment variables

```env
AZURE_SEARCH_ENDPOINT=https://<your-search-service>.search.windows.net
AZURE_SEARCH_ADMIN_KEY=<admin-key>
AZURE_SEARCH_INDEX_NAME=supply-chain-docs
AZURE_SEARCH_API_VERSION=2024-07-01
```

## Bootstrap the demo index

The project includes seed documents in:

```text
data/azure_search_seed_documents.json
```

Create or update the index and upload the demo documents with:

```powershell
python scripts/bootstrap_azure_search.py
```

The script uses Azure AI Search REST APIs directly through `requests`, so no extra Azure Search SDK is required.

## Validate

Start the APIs and call:

```powershell
curl.exe http://localhost:8001/data-source-status
curl.exe http://localhost:8002/data-source-status
```

Expected behavior:

- without Azure AI Search config: `structured_data_source = local_reference_data`;
- with Azure AI Search config: `structured_data_source = azure_ai_search`.

## Index fields

The seed index uses these fields:

- `id`
- `title`
- `agent`
- `doc_type`
- `entity_type`
- `entity_id`
- `source`
- `content`
- `payload_json`

`payload_json` contains the structured source-of-truth payload used by deterministic endpoints.


## Document RAG layer

The project now also supports document chunks in the same Azure AI Search index.

Example source documents live in:

```text
data/documents/
```

They represent small corporate knowledge artifacts such as:

- inventory policy;
- supplier contract notes;
- supplier performance policy;
- urgent order logistics procedure.

Ingest them with:

```powershell
python scripts/ingest_knowledge_documents.py
```

Preview what will be uploaded without writing to Azure Search:

```powershell
python scripts/ingest_knowledge_documents.py --dry-run
```

The ingestion script creates records with:

```text
doc_type=document_chunk
entity_type=knowledge
```

These are used by the document-RAG endpoints and by the Azure AI Search tool exposed to the agents.

## Document-RAG endpoints

Inventory Agent:

```powershell
curl.exe -X POST http://localhost:8001/knowledge/search -H "Content-Type: application/json" -d "{\"question\":\"O que a política diz sobre estoque crítico?\"}"
```

Supplier Agent:

```powershell
curl.exe -X POST http://localhost:8002/knowledge/search -H "Content-Type: application/json" -d "{\"question\":\"Quais são as condições contratuais da XYZ Metais?\"}"
```

Supervisor Agent:

```powershell
curl.exe -X POST http://localhost:8000/knowledge/search -H "Content-Type: application/json" -d "{\"question\":\"Existe alguma regra para compras urgentes?\"}"
```

On Windows PowerShell, `Invoke-RestMethod` is usually safer for Portuguese text:

```powershell
$body = @{ question = "O que a política diz sobre estoque crítico?" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/knowledge/search" -Method POST -ContentType "application/json; charset=utf-8" -Body $body
```

## Current RAG maturity

This layer is the first document-RAG implementation:

1. local documents;
2. chunking;
3. upload to Azure AI Search;
4. keyword/hybrid-ready retrieval through `/knowledge/search`;
5. LLM grounding through the existing `search_azure_ai_search` tool.

The next evolution is adding a vector field and embeddings to the index so retrieval can use vector queries plus keyword search.

## Document RAG answer generation

The document RAG layer now has two supervisor endpoints:

- `POST /knowledge/search`: returns raw retrieved chunks from Azure AI Search. Use this for debugging retrieval quality.
- `POST /knowledge/answer`: retrieves chunks and uses the configured LLM to generate a final grounded answer in Portuguese.

Example:

```powershell
$body = @{ question = "O que a política diz sobre estoque crítico?" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/knowledge/answer" -Method POST -ContentType "application/json; charset=utf-8" -Body $body
```

The normal `/copilot` endpoint can also route open-ended policy, procedure, contract and document questions to the `knowledge` route automatically.

Example:

```powershell
$body = @{ question = "O que a política diz sobre estoque crítico?" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/copilot" -Method POST -ContentType "application/json; charset=utf-8" -Body $body
```

Expected flow:

```text
User question
  -> Supervisor route: knowledge
  -> Azure AI Search document chunks
  -> LLM grounded answer
  -> Response with sources
```
