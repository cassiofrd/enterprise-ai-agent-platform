from __future__ import annotations

import os
from typing import Any

import requests


DEFAULT_API_VERSION = "2024-07-01"


def get_azure_search_endpoint() -> str | None:
    return os.getenv("AZURE_SEARCH_ENDPOINT")


def get_azure_search_key() -> str | None:
    return os.getenv("AZURE_SEARCH_ADMIN_KEY")


def get_azure_search_index_name() -> str:
    return os.getenv("AZURE_SEARCH_INDEX_NAME", "supply-chain-docs")


def azure_search_enabled() -> bool:
    return bool(get_azure_search_endpoint() and get_azure_search_key())


def search_supply_chain_docs(
    query: str,
    agent: str | None = None,
    top: int = 3,
) -> list[dict[str, Any]]:
    endpoint = get_azure_search_endpoint()
    key = get_azure_search_key()
    index_name = get_azure_search_index_name()

    if not endpoint or not key:
        return []

    endpoint = endpoint.rstrip("/")
    url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={DEFAULT_API_VERSION}"

    body: dict[str, Any] = {
        "search": query,
        "top": top,
        "select": "id,title,agent,content",
    }

    if agent:
        body["filter"] = f"agent eq '{agent}'"

    headers = {
        "Content-Type": "application/json",
        "api-key": key,
    }

    response = requests.post(url, headers=headers, json=body, timeout=30)
    response.raise_for_status()

    payload = response.json()
    return payload.get("value", [])


def format_search_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return "No relevant Azure AI Search documents found."

    formatted = []

    for index, item in enumerate(results, start=1):
        title = item.get("title", "Untitled")
        agent = item.get("agent", "unknown")
        score = item.get("@search.score")
        content = item.get("content", "")

        formatted.append(
            f"[Result {index}] Title={title} Agent={agent} Score={score}\n{content}"
        )

    return "Relevant Azure AI Search documents:\n\n" + "\n\n---\n\n".join(formatted)
