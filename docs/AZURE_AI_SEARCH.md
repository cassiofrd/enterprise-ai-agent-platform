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
