from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

API_VERSION = os.getenv("AZURE_SEARCH_API_VERSION", "2024-07-01")
ENDPOINT = (os.getenv("AZURE_SEARCH_ENDPOINT") or "").rstrip("/")
ADMIN_KEY = os.getenv("AZURE_SEARCH_ADMIN_KEY") or ""
INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX_NAME", "supply-chain-docs")
SEED_PATH = PROJECT_ROOT / "data" / "azure_search_seed_documents.json"


INDEX_SCHEMA: dict[str, Any] = {
    "name": INDEX_NAME,
    "fields": [
        {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
        {"name": "title", "type": "Edm.String", "searchable": True, "filterable": True},
        {"name": "agent", "type": "Edm.String", "searchable": True, "filterable": True, "facetable": True},
        {"name": "doc_type", "type": "Edm.String", "searchable": True, "filterable": True, "facetable": True},
        {"name": "entity_type", "type": "Edm.String", "searchable": True, "filterable": True, "facetable": True},
        {"name": "entity_id", "type": "Edm.String", "searchable": True, "filterable": True, "facetable": True},
        {"name": "source", "type": "Edm.String", "searchable": True, "filterable": True},
        {"name": "content", "type": "Edm.String", "searchable": True},
        {"name": "payload_json", "type": "Edm.String", "searchable": True},
    ],
}


def require_config() -> None:
    missing = []
    if not ENDPOINT:
        missing.append("AZURE_SEARCH_ENDPOINT")
    if not ADMIN_KEY:
        missing.append("AZURE_SEARCH_ADMIN_KEY")
    if missing:
        raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")


def headers() -> dict[str, str]:
    return {"Content-Type": "application/json", "api-key": ADMIN_KEY}


def create_or_update_index() -> None:
    url = f"{ENDPOINT}/indexes/{INDEX_NAME}?api-version={API_VERSION}"
    response = requests.put(url, headers=headers(), json=INDEX_SCHEMA, timeout=60)
    response.raise_for_status()
    print(f"Index ready: {INDEX_NAME}")


def load_seed_documents() -> list[dict[str, Any]]:
    with SEED_PATH.open("r", encoding="utf-8") as f:
        docs = json.load(f)
    for doc in docs:
        doc["@search.action"] = "mergeOrUpload"
    return docs


def upload_documents(docs: list[dict[str, Any]]) -> None:
    url = f"{ENDPOINT}/indexes/{INDEX_NAME}/docs/index?api-version={API_VERSION}"
    response = requests.post(url, headers=headers(), json={"value": docs}, timeout=60)
    response.raise_for_status()
    payload = response.json()
    failures = [item for item in payload.get("value", []) if not item.get("succeeded")]
    if failures:
        raise SystemExit(f"Some documents failed to upload: {failures}")
    print(f"Uploaded {len(docs)} documents to {INDEX_NAME}")


def main() -> None:
    require_config()
    print(f"Azure AI Search endpoint: {ENDPOINT}")
    print(f"Index: {INDEX_NAME}")
    create_or_update_index()
    upload_documents(load_seed_documents())
    print("Bootstrap completed.")


if __name__ == "__main__":
    main()
