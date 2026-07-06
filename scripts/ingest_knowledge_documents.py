from __future__ import annotations

import argparse
import hashlib
import os
import re
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
DOCUMENTS_DIR = PROJECT_ROOT / "data" / "documents"


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


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def infer_agent(path: Path, text: str) -> str:
    name = path.name.lower()
    content = text.lower()
    if "supplier" in name or "fornecedor" in content or "contrato" in content:
        return "supplier"
    if "inventory" in name or "estoque" in content or "parafuso" in content:
        return "inventory"
    if "logistic" in name or "logística" in content:
        return "supervisor"
    return "supervisor"


def infer_title(path: Path, text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return path.stem.replace("_", " ").replace("-", " ").title()


def chunk_text(text: str, *, chunk_size: int = 900, overlap: int = 150) -> list[str]:
    normalized = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(normalized) <= chunk_size:
        return [normalized]

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        window = normalized[start:end]

        # Prefer breaking at paragraph or sentence boundaries.
        if end < len(normalized):
            paragraph_break = window.rfind("\n\n")
            sentence_break = max(window.rfind(". "), window.rfind("; "), window.rfind("\n- "))
            break_at = paragraph_break if paragraph_break > chunk_size * 0.55 else sentence_break
            if break_at > chunk_size * 0.45:
                end = start + break_at + 1
                window = normalized[start:end]

        chunks.append(window.strip())
        if end >= len(normalized):
            break
        start = max(0, end - overlap)

    return [chunk for chunk in chunks if chunk]


def load_documents() -> list[dict[str, Any]]:
    if not DOCUMENTS_DIR.exists():
        raise SystemExit(f"Documents directory not found: {DOCUMENTS_DIR}")

    docs: list[dict[str, Any]] = []

    for path in sorted(DOCUMENTS_DIR.glob("*")):
        if path.suffix.lower() not in {".md", ".txt"}:
            continue

        text = path.read_text(encoding="utf-8")
        title = infer_title(path, text)
        agent = infer_agent(path, text)
        source = f"data/documents/{path.name}"

        for i, chunk in enumerate(chunk_text(text), start=1):
            key_source = f"{path.name}:{i}:{chunk[:80]}"
            digest = hashlib.sha1(key_source.encode("utf-8")).hexdigest()[:12]
            docs.append(
                {
                    "@search.action": "mergeOrUpload",
                    "id": f"doc-{slugify(path.stem)}-{i}-{digest}",
                    "title": f"{title} - chunk {i}",
                    "agent": agent,
                    "doc_type": "document_chunk",
                    "entity_type": "knowledge",
                    "entity_id": slugify(path.stem).upper(),
                    "source": source,
                    "content": chunk,
                    "payload_json": "",
                }
            )

    return docs


def upload_documents(docs: list[dict[str, Any]]) -> None:
    url = f"{ENDPOINT}/indexes/{INDEX_NAME}/docs/index?api-version={API_VERSION}"
    response = requests.post(url, headers=headers(), json={"value": docs}, timeout=60)
    response.raise_for_status()

    payload = response.json()
    failures = [
        item for item in payload.get("value", [])
        if not item.get("succeeded") and not item.get("status", False)
    ]
    if failures:
        raise SystemExit(f"Some documents failed to upload: {failures}")

    print(f"Uploaded {len(docs)} document chunks to index {INDEX_NAME}.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest local knowledge documents into Azure AI Search.")
    parser.add_argument("--dry-run", action="store_true", help="Only print generated chunks without uploading.")
    args = parser.parse_args()

    require_config()
    docs = load_documents()

    if not docs:
        raise SystemExit(f"No .md or .txt documents found in {DOCUMENTS_DIR}")

    print(f"Azure AI Search endpoint: {ENDPOINT}")
    print(f"Index: {INDEX_NAME}")
    print(f"Document chunks prepared: {len(docs)}")

    if args.dry_run:
        for doc in docs:
            print(f"- {doc['id']} | {doc['agent']} | {doc['title']}")
        return

    upload_documents(docs)
    print("Document ingestion completed.")


if __name__ == "__main__":
    main()
