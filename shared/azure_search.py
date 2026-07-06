from __future__ import annotations

import json
import os
from typing import Any

import requests


DEFAULT_API_VERSION = os.getenv("AZURE_SEARCH_API_VERSION", "2024-07-01")


def get_azure_search_endpoint() -> str | None:
    return os.getenv("AZURE_SEARCH_ENDPOINT")


def get_azure_search_key() -> str | None:
    return os.getenv("AZURE_SEARCH_ADMIN_KEY")


def get_azure_search_index_name() -> str:
    return os.getenv("AZURE_SEARCH_INDEX_NAME", "supply-chain-docs")


def azure_search_enabled() -> bool:
    return bool(get_azure_search_endpoint() and get_azure_search_key())


def azure_search_status() -> dict[str, Any]:
    return {
        "enabled": azure_search_enabled(),
        "endpoint_configured": bool(get_azure_search_endpoint()),
        "key_configured": bool(get_azure_search_key()),
        "index_name": get_azure_search_index_name(),
        "api_version": DEFAULT_API_VERSION,
    }


def _search_url() -> str | None:
    endpoint = get_azure_search_endpoint()
    if not endpoint:
        return None
    endpoint = endpoint.rstrip("/")
    index_name = get_azure_search_index_name()
    return f"{endpoint}/indexes/{index_name}/docs/search?api-version={DEFAULT_API_VERSION}"


def _headers() -> dict[str, str] | None:
    key = get_azure_search_key()
    if not key:
        return None
    return {
        "Content-Type": "application/json",
        "api-key": key,
    }


def _escape_odata(value: str) -> str:
    return value.replace("'", "''")


def _and_filters(parts: list[str | None]) -> str | None:
    cleaned = [part for part in parts if part]
    if not cleaned:
        return None
    return " and ".join(cleaned)


def search_supply_chain_docs(
    query: str,
    agent: str | None = None,
    top: int = 3,
    filter_expression: str | None = None,
) -> list[dict[str, Any]]:
    """Run a keyword search over Azure AI Search.

    The project intentionally uses the REST API instead of azure-search-documents
    so the same code works in local development, Cloud Shell, and Container Apps
    without introducing another SDK dependency.
    """
    url = _search_url()
    headers = _headers()

    if not url or not headers:
        return []

    filters: list[str | None] = [filter_expression]
    if agent:
        filters.append(f"agent eq '{_escape_odata(agent)}'")

    body: dict[str, Any] = {
        "search": query or "*",
        "top": top,
        "select": "id,title,agent,doc_type,entity_type,entity_id,content,payload_json,source",
    }

    final_filter = _and_filters(filters)
    if final_filter:
        body["filter"] = final_filter

    response = requests.post(url, headers=headers, json=body, timeout=30)
    response.raise_for_status()

    payload = response.json()
    return payload.get("value", [])



def search_knowledge_chunks(
    query: str,
    agent: str | None = None,
    top: int = 5,
) -> list[dict[str, Any]]:
    """Search document chunks indexed in Azure AI Search.

    This is the document-RAG layer. Structured lookups continue to use
    entity_type/entity_id filters; open-ended policy, contract, procedure, and
    guidance questions should use this function.
    """
    filter_expression = "doc_type eq 'document_chunk'"
    return search_supply_chain_docs(
        query=query,
        agent=agent,
        top=top,
        filter_expression=filter_expression,
    )


def format_knowledge_context(results: list[dict[str, Any]]) -> str:
    """Format search results as a grounded context block for an LLM."""
    if not results:
        return "No relevant document chunks were found in Azure AI Search."

    blocks: list[str] = []
    for index, item in enumerate(results, start=1):
        title = item.get("title", "Untitled")
        source = item.get("source", "unknown")
        agent = item.get("agent", "unknown")
        doc_type = item.get("doc_type", "unknown")
        score = item.get("@search.score")
        content = item.get("content", "")
        blocks.append(
            f"[Chunk {index}] title={title} source={source} agent={agent} "
            f"doc_type={doc_type} score={score}\n{content}"
        )

    return "Grounding document chunks from Azure AI Search:\n\n" + "\n\n---\n\n".join(blocks)


def answer_from_knowledge(
    *,
    question: str,
    agent: str | None = None,
    top: int = 5,
) -> dict[str, Any]:
    """Return document-RAG context and raw hits for a question.

    The API layer can pass the formatted context to the LLM or return it directly
    to callers such as Azure AI Foundry tools.
    """
    results = search_knowledge_chunks(query=question, agent=agent, top=top)
    return {
        "question": question,
        "agent_filter": agent,
        "result_count": len(results),
        "context": format_knowledge_context(results),
        "results": results,
    }


def lookup_structured_entity(
    *,
    entity_type: str,
    entity_id: str,
    agent: str | None = None,
) -> dict[str, Any] | None:
    """Lookup a structured entity document and return its parsed payload_json.

    Expected index fields:
    - entity_type: product, supplier, contract, policy, etc.
    - entity_id: normalized business identifier, such as PARAFUSO-M20 or XYZ METAIS
    - payload_json: JSON string containing the structured source-of-truth payload
    """
    if not azure_search_enabled() or not entity_id:
        return None

    normalized_entity_id = entity_id.strip().upper()
    filter_parts = [
        f"entity_type eq '{_escape_odata(entity_type)}'",
        f"entity_id eq '{_escape_odata(normalized_entity_id)}'",
    ]
    filter_expression = _and_filters(filter_parts)

    results = search_supply_chain_docs(
        query="*",
        agent=agent,
        top=1,
        filter_expression=filter_expression,
    )

    if not results:
        return None

    payload_json = results[0].get("payload_json")
    if not payload_json:
        return None

    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return None

    if isinstance(payload, dict):
        payload.setdefault("source", "azure_ai_search")
        return payload

    return None


def format_search_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return "No relevant Azure AI Search documents found."

    formatted = []

    for index, item in enumerate(results, start=1):
        title = item.get("title", "Untitled")
        agent = item.get("agent", "unknown")
        doc_type = item.get("doc_type", "unknown")
        entity_type = item.get("entity_type", "unknown")
        entity_id = item.get("entity_id", "unknown")
        score = item.get("@search.score")
        content = item.get("content", "")

        formatted.append(
            f"[Result {index}] Title={title} Agent={agent} DocType={doc_type} "
            f"Entity={entity_type}:{entity_id} Score={score}\n{content}"
        )

    return "Relevant Azure AI Search documents:\n\n" + "\n\n---\n\n".join(formatted)
