from __future__ import annotations

import asyncio
import json
import re
import time

from fastapi import Depends, FastAPI, Query, HTTPException
from pydantic import BaseModel

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.messages.tool import ToolMessage
from langchain_core.tools import tool
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS

from shared.cache import cache_backend, cache_get, cache_set
from shared.memory import save_memory, search_memories, list_memories, delete_memory
from shared.config import (
    KNOWLEDGE_BASE_PATH,
    MCP_INVENTORY_SERVER_MODULE,
    VECTOR_INDEX_PATH,
)
from shared.settings import settings
from shared.observability import (
    get_metrics_summary,
    get_recent_events,
    get_trace_events,
    get_trace_index,
    get_trace_summary,
    log_event,
    log_llm_usage,
    new_trace_id,
    observe_duration,
)
from shared.schemas import InventoryRequest
from shared.azure_search import (
    azure_search_enabled,
    azure_search_status,
    answer_from_knowledge,
    format_knowledge_context,
    format_search_results,
    lookup_structured_entity,
    search_knowledge_chunks,
    search_supply_chain_docs,
)
from shared.llm import get_chat_llm, get_embeddings
from shared.auth import require_auth



ACTIVE_CHAT_MODEL = settings.active_chat_model or "gpt-4o-mini"
ACTIVE_EMBEDDING_MODEL = settings.active_embedding_model or "text-embedding-3-small"
AZURE_SEARCH_ENDPOINT = settings.azure_search_endpoint
AZURE_SEARCH_INDEX_NAME = (
    settings.azure_search_index_name
    or settings.azure_search_index
    or "supply-chain-docs"
)

app = FastAPI(title="Inventory Agent Server with MCP, Vector RAG and Observability")


class KnowledgeSearchRequest(BaseModel):
    question: str
    top: int = 5


_vector_store = None


# Structured inventory reference data exposed as simple REST tool endpoints.
# These endpoints are designed for OpenAPI-based tool calling platforms such as
# Azure AI Foundry, Copilot Studio, and other external clients. They do not
# replace /invoke; they expose specific business capabilities in a predictable
# format.
ABC_POLICIES = {
    "A": {
        "safety_stock_units": 500,
        "replenishment_frequency": "weekly",
        "review_frequency": "daily",
        "critical_level_units": 300,
    },
    "B": {
        "safety_stock_units": 200,
        "replenishment_frequency": "biweekly",
        "review_frequency": "weekly",
        "critical_level_units": 100,
    },
    "C": {
        "safety_stock_units": 50,
        "replenishment_frequency": "monthly",
        "review_frequency": "monthly",
        "critical_level_units": 20,
    },
}

PRODUCT_CATALOG = {
    "PARAFUSO-M10": {
        "code": "PARAFUSO-M10",
        "product_name": "PARAFUSO-M10",
        "abc_class": "A",
        "preferred_supplier": "ABC Componentes Industriais",
        "lead_time_days": 7,
    },
    "PARAFUSO-M20": {
        "code": "PARAFUSO-M20",
        "product_name": "PARAFUSO-M20",
        "abc_class": "B",
        "preferred_supplier": "XYZ Metais",
        "lead_time_days": 14,
    },
    "PORCA-M10": {
        "code": "PORCA-M10",
        "product_name": "PORCA-M10",
        "abc_class": "C",
        "preferred_supplier": "DEF Fixadores",
        "lead_time_days": 30,
    },
}

PURCHASING_POLICY = {
    "approval_threshold_brl": 50000,
    "approval_required_above_threshold": True,
    "approval_role": "manager",
    "urgent_orders_without_additional_quote_when_critical": True,
}


def normalize_product_code(code: str) -> str:
    return code.strip().replace("_", "-").upper()


def get_product_from_azure_search(code: str) -> dict | None:
    """Return product data from Azure AI Search when configured.

    This is the first step toward replacing local demo dictionaries with a
    searchable corporate knowledge layer. The local PRODUCT_CATALOG remains the
    deterministic fallback for local development and automated tests.
    """
    normalized_code = normalize_product_code(code)
    product = lookup_structured_entity(
        entity_type="product",
        entity_id=normalized_code,
        agent="inventory",
    )
    if not product:
        return None

    return {
        "code": product.get("code", normalized_code),
        "product_name": product.get("product_name", normalized_code),
        "abc_class": product.get("abc_class", "C"),
        "preferred_supplier": product.get("preferred_supplier"),
        "lead_time_days": product.get("lead_time_days"),
        "source": product.get("source", "azure_ai_search"),
    }


def get_product_or_404(code: str) -> dict:
    normalized_code = normalize_product_code(code)

    product = get_product_from_azure_search(normalized_code)
    if product is not None:
        return product

    product = PRODUCT_CATALOG.get(normalized_code)
    if product is None:
        raise HTTPException(
            status_code=404,
            detail=f"Product not found: {normalized_code}",
        )
    return product



def log_openapi_tool_call(
    *,
    endpoint: str,
    method: str = "GET",
    status: str,
    http_status_code: int,
    latency_ms: float,
    **fields,
) -> None:
    """Log deterministic OpenAPI tool endpoint calls.

    These events are used to observe when Azure AI Foundry or another client
    calls the structured REST capabilities exposed by the Inventory Agent.
    """
    log_event(
        "api.openapi_tool.call",
        agent="inventory",
        endpoint=endpoint,
        method=method,
        status=status,
        http_status_code=http_status_code,
        latency_ms=round(latency_ms, 2),
        **fields,
    )


def build_product_payload(product: dict) -> dict:
    abc_class = product["abc_class"]
    policy = ABC_POLICIES[abc_class]
    return {
        **product,
        "inventory_policy": policy,
        "source": product.get("source", "structured_inventory_reference_data"),
    }



def normalize_rag_cache_key(query: str) -> str:
    normalized = " ".join(query.strip().lower().split())

    replacements = {
        "backorders": "backorder",
        "thresholds": "threshold",
        "policies": "policy",
        "rules": "rule",
        "warehouses": "warehouse",
        "replenishments": "replenishment",
        "orders": "order",
    }

    words = [replacements.get(word, word) for word in normalized.split()]
    return " ".join(words)


def load_inventory_knowledge_base() -> str:
    if not KNOWLEDGE_BASE_PATH.exists():
        return "Inventory knowledge base file not found."
    return KNOWLEDGE_BASE_PATH.read_text(encoding="utf-8")


def build_or_load_vector_store():
    global _vector_store

    if _vector_store is not None:
        return _vector_store

    embeddings = get_embeddings()

    if VECTOR_INDEX_PATH.exists():
        log_event("rag.index.load", index_path=str(VECTOR_INDEX_PATH))
        _vector_store = FAISS.load_local(
            str(VECTOR_INDEX_PATH),
            embeddings,
            allow_dangerous_deserialization=True,
        )
        return _vector_store

    knowledge = load_inventory_knowledge_base()
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=80)
    chunks = text_splitter.split_text(knowledge)

    metadatas = [
        {"source": str(KNOWLEDGE_BASE_PATH), "chunk": i}
        for i, _ in enumerate(chunks)
    ]

    log_event(
        "rag.index.build",
        source=str(KNOWLEDGE_BASE_PATH),
        chunks=len(chunks),
        embedding_model=ACTIVE_EMBEDDING_MODEL,
    )

    _vector_store = FAISS.from_texts(
        texts=chunks,
        embedding=embeddings,
        metadatas=metadatas,
    )

    VECTOR_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    _vector_store.save_local(str(VECTOR_INDEX_PATH))

    return _vector_store



def get_latest_human_message(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if message.type == "human":
            return str(message.content)
    return ""


def extract_sku(text: str) -> str | None:
    """Extract product/SKU identifiers from free text.

    The first version only handled values like SKU-123. Corporate
    inventory data often uses business codes such as PARAFUSO-M20,
    TONER-HP-CF281A, FURADEIRA-BOSCH-18V, or ROLAMENTO-6205.
    """
    product_codes = extract_product_codes(text)
    if product_codes:
        return product_codes[0]
    return None


def extract_product_codes(text: str) -> list[str]:
    """Return normalized product/SKU-like codes found in the message."""
    if not text:
        return []

    patterns = [
        r"\bSKU[-_ ]?[A-Za-z0-9]+\b",
        r"\b[A-ZÁÉÍÓÚÃÕÇ]{2,}(?:[-_][A-Z0-9ÁÉÍÓÚÃÕÇ]+)+\b",
        r"\b[A-ZÁÉÍÓÚÃÕÇ]{2,}[-_][A-Z0-9ÁÉÍÓÚÃÕÇ]+(?:[-_][A-Z0-9ÁÉÍÓÚÃÕÇ]+)*\b",
    ]

    found: list[str] = []
    seen: set[str] = set()

    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            code = match.group(0).replace(" ", "-").replace("_", "-").upper()
            if code not in seen:
                seen.add(code)
                found.append(code)

    return found


def is_memory_save_request(text: str) -> bool:
    normalized = text.strip().lower()
    memory_triggers = [
        "remember that",
        "remember:",
        "save that",
        "store that",
        "keep in memory",
        "memorize that",
        "registre que",
        "registrar que",
        "salve que",
        "salvar que",
        "guarde que",
        "guardar que",
        "lembre que",
        "lembrar que",
        "memorize que",
        "memorizar que",
    ]
    return any(trigger in normalized for trigger in memory_triggers)


def extract_preferred_supplier_fact(text: str) -> tuple[str, str] | None:
    """Extract facts like 'fornecedor do PARAFUSO-M20 é XYZ Metais'."""
    patterns = [
        r"fornecedor(?:\s+preferencial)?\s+(?:do|da|de)\s+(?P<product>[A-ZÁÉÍÓÚÃÕÇ0-9_-]+(?:-[A-ZÁÉÍÓÚÃÕÇ0-9_-]+)*)\s+(?:é|eh|e|=|será|sera)\s+(?P<supplier>[^.;\n]+)",
        r"supplier(?:\s+for)?\s+(?P<product>[A-Z0-9_-]+(?:-[A-Z0-9_-]+)*)\s+(?:is|=)\s+(?P<supplier>[^.;\n]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            product = match.group("product").replace("_", "-").upper().strip()
            supplier = match.group("supplier").strip().strip('"\'')
            return product, supplier

    return None


def infer_memory_type(text: str) -> str:
    normalized = text.lower()
    if "fornecedor" in normalized or "supplier" in normalized:
        return "supplier"
    if "cliente" in normalized or "customer" in normalized:
        return "customer"
    if "pedido" in normalized or "order" in normalized:
        return "order"
    if "estoque" in normalized or "stock" in normalized or "inventory" in normalized:
        return "inventory"
    return "fact"


def build_memory_key(text: str) -> str:
    supplier_fact = extract_preferred_supplier_fact(text)
    if supplier_fact:
        product, _supplier = supplier_fact
        return f"inventory:supplier:{product}"

    sku = extract_sku(text)
    normalized = "_".join(text.strip().lower().split())[:80]
    memory_type = infer_memory_type(text)

    if sku:
        return f"inventory:{memory_type}:{sku}"

    return f"inventory:{memory_type}:{normalized}"


def build_memory_value(text: str) -> str:
    supplier_fact = extract_preferred_supplier_fact(text)
    if supplier_fact:
        product, supplier = supplier_fact
        return json.dumps(
            {
                "type": "supplier",
                "entity": product,
                "value": supplier,
                "original_text": text,
            },
            ensure_ascii=False,
        )

    sku = extract_sku(text)
    return json.dumps(
        {
            "type": infer_memory_type(text),
            "entity": sku,
            "value": text,
            "original_text": text,
        },
        ensure_ascii=False,
    )


def build_memory_search_queries(user_message: str) -> list[str]:
    queries: list[str] = []

    def add(query: str | None) -> None:
        if not query:
            return
        query = query.strip()
        if query and query not in queries:
            queries.append(query)

    add(user_message)

    for product_code in extract_product_codes(user_message):
        add(product_code)
        add(product_code.lower())
        add(f"inventory:supplier:{product_code}")
        add(f"inventory:inventory:{product_code}")
        add(f"inventory:fact:{product_code}")

    supplier_fact = extract_preferred_supplier_fact(user_message)
    if supplier_fact:
        product, supplier = supplier_fact
        add(product)
        add(supplier)

    return queries


def build_long_term_memory_context(user_message: str) -> str:
    if not user_message:
        return "No user message available for memory lookup."

    queries = build_memory_search_queries(user_message)

    collected: list[dict] = []
    seen_ids: set[str] = set()

    for query in queries:
        try:
            memories = search_memories(query=query, limit=5)
        except Exception as exc:
            log_event(
                "memory.context.error",
                agent="inventory",
                query=query,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            continue

        for memory in memories:
            memory_id = str(memory.get("id"))
            if memory_id in seen_ids:
                continue
            seen_ids.add(memory_id)
            collected.append(memory)

    log_event(
        "memory.context.search",
        agent="inventory",
        query=user_message,
        result_count=len(collected),
    )

    if not collected:
        return "No relevant long-term memories found."

    return json.dumps(collected[:5], ensure_ascii=False, indent=2)


@tool
def search_inventory_knowledge_base(query: str) -> str:
    """Use vector search over the inventory knowledge base for general policies, thresholds, warehouse rules, and replenishment guidance."""
    print(f"[TOOL][Vector RAG] search_inventory_knowledge_base(query={query})")
    log_event("tool.start", tool="search_inventory_knowledge_base", query=query)

    cache_key = normalize_rag_cache_key(query)
    backend = cache_backend()
    cached_result = cache_get(cache_key)

    if cached_result is not None:
        log_event(
            "rag.cache.hit",
            agent="inventory",
            tool="search_inventory_knowledge_base",
            query=query,
            cache_key=cache_key,
            backend=backend,
        )
        return cached_result

    log_event(
        "rag.cache.miss",
        agent="inventory",
        tool="search_inventory_knowledge_base",
        query=query,
        cache_key=cache_key,
        backend=backend,
    )

    with observe_duration(
        "rag.search.duration",
        agent="inventory",
        tool="search_inventory_knowledge_base",
        query=query,
    ):
        vector_store = build_or_load_vector_store()
        docs_and_scores = vector_store.similarity_search_with_score(query, k=3)

    if not docs_and_scores:
        result = "No relevant inventory knowledge base content found."
        log_event("rag.search", query=query, result_count=0)

        cache_set(cache_key, result)

        log_event(
            "rag.cache.store",
            agent="inventory",
            tool="search_inventory_knowledge_base",
            query=query,
            cache_key=cache_key,
            backend=cache_backend(),
        )
        return result

    formatted_docs = []
    for i, (doc, score) in enumerate(docs_and_scores, start=1):
        formatted_docs.append(
            f"[Result {i}] Source={doc.metadata.get('source')} "
            f"Chunk={doc.metadata.get('chunk')} Score={score}\n{doc.page_content}"
        )

    result = "Relevant inventory knowledge base excerpts:\n\n" + "\n\n---\n\n".join(
        formatted_docs
    )

    log_event(
        "rag.search",
        query=query,
        result_count=len(docs_and_scores),
        results=[
            {
                "source": doc.metadata.get("source"),
                "chunk": doc.metadata.get("chunk"),
                "score": float(score),
            }
            for doc, score in docs_and_scores
        ],
    )

    cache_set(cache_key, result)

    log_event(
        "rag.cache.store",
        agent="inventory",
        tool="search_inventory_knowledge_base",
        query=query,
        cache_key=cache_key,
        backend=cache_backend(),
    )

    return result


async def call_mcp_reorder_policy(sku: str) -> str:
    server_params = StdioServerParameters(
        command="python",
        args=["-m", MCP_INVENTORY_SERVER_MODULE],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("get_reorder_policy", {"sku": sku})
            return result.content[0].text


@tool
def get_reorder_policy_via_mcp(sku: str) -> str:
    """Retrieve SKU-specific reorder policy information from the MCP inventory server."""
    print(f"[TOOL][Inventory Agent Server] get_reorder_policy_via_mcp(sku={sku})")
    log_event("tool.start", tool="get_reorder_policy_via_mcp", sku=sku)

    with observe_duration(
        "mcp.reorder_policy.duration",
        agent="inventory",
        tool="get_reorder_policy_via_mcp",
        sku=sku,
    ):
        result = asyncio.run(call_mcp_reorder_policy(sku))

    log_event(
        "tool.end",
        tool="get_reorder_policy_via_mcp",
        sku=sku,
        result_preview=result[:300],
    )
    return result


@tool
def manage_inventory(sku: str = None, **kwargs) -> str:
    """Manage inventory levels, stock replenishment, audits, and optimization strategies."""
    print(f"[TOOL][Inventory Agent Server] manage_inventory(sku={sku}, kwargs={kwargs})")
    log_event("tool.start", tool="manage_inventory", sku=sku, kwargs=kwargs)
    return "inventory_management_initiated"


@tool
def forecast_demand(season: str = None, **kwargs) -> str:
    """Analyze demand patterns, seasonal trends, and create forecasting models."""
    print(f"[TOOL][Inventory Agent Server] forecast_demand(season={season}, kwargs={kwargs})")
    log_event("tool.start", tool="forecast_demand", season=season, kwargs=kwargs)
    return "demand_forecast_generated"


@tool
def send_logistics_response(operation_id: str = None, message: str = None) -> str:
    """Send logistics updates, recommendations, or status reports to stakeholders."""
    print(
        f"[TOOL][Inventory Agent Server] send_logistics_response("
        f"operation_id={operation_id}, message={message})"
    )
    log_event(
        "tool.start",
        tool="send_logistics_response",
        operation_id=operation_id,
        message=message,
    )
    return "logistics_response_sent"


@tool
def save_agent_memory(key: str, value: str) -> str:
    """Save an important long-term memory for future inventory and warehouse conversations.

    This tool normalizes LLM-provided key/value pairs. Even if the LLM passes
    a weak key such as "supplier", the tool attempts to extract structured
    product facts from the combined key/value text and stores a searchable key
    like inventory:supplier:PARAFUSO-M20.
    """
    print(f"[TOOL][Long-Term Memory] save_agent_memory(key={key}, value={value})")

    raw_key = key or ""
    raw_value = value or ""
    combined_text = f"{raw_key} {raw_value}".strip()

    normalized_key = raw_key.strip() or build_memory_key(combined_text)
    normalized_value = raw_value.strip()
    normalized_type = infer_memory_type(combined_text)

    # Prefer a structured supplier memory when the fact is present anywhere in
    # the tool arguments. This makes retrieval deterministic for product codes.
    supplier_fact = extract_preferred_supplier_fact(combined_text)
    if supplier_fact:
        product, _supplier = supplier_fact
        normalized_key = f"inventory:supplier:{product}"
        normalized_value = build_memory_value(combined_text)
        normalized_type = "supplier"
    else:
        product_codes = extract_product_codes(combined_text)
        if product_codes and not any(code in normalized_key.upper() for code in product_codes):
            normalized_key = f"inventory:{normalized_type}:{product_codes[0]}"
        if not normalized_value:
            normalized_value = build_memory_value(combined_text)

    memory_id = save_memory(
        key=normalized_key,
        value=normalized_value,
        memory_type=normalized_type,
        source_agent="inventory",
    )

    log_event(
        "memory.save",
        agent="inventory",
        raw_key=raw_key,
        normalized_key=normalized_key,
        memory_type=normalized_type,
        memory_id=memory_id,
    )

    return f"Memory saved successfully. id={memory_id}; key={normalized_key}"


@tool
def search_agent_memory(query: str) -> str:
    """Search long-term memories previously saved by the agent.

    The search expands product/entity queries such as PARAFUSO-M20 into the
    structured keys used by save_agent_memory.
    """
    print(f"[TOOL][Long-Term Memory] search_agent_memory(query={query})")

    collected: list[dict] = []
    seen_ids: set[str] = set()

    for expanded_query in build_memory_search_queries(query):
        for memory in search_memories(query=expanded_query, limit=5):
            memory_id = str(memory.get("id"))
            if memory_id in seen_ids:
                continue
            seen_ids.add(memory_id)
            collected.append(memory)

    log_event(
        "memory.search",
        agent="inventory",
        query=query,
        expanded_queries=build_memory_search_queries(query),
        result_count=len(collected),
    )

    if not collected:
        return "No relevant long-term memories found."

    return "Relevant long-term memories:\n" + json.dumps(
        collected[:5],
        ensure_ascii=False,
        indent=2,
    )


@tool
def search_azure_ai_search(query: str) -> str:
    """Search Azure AI Search for supply chain documents related to inventory, policies, suppliers, and operational guidance."""
    print(f"[TOOL][Azure AI Search] search_azure_ai_search(query={query})")
    log_event(
        "azure_search.start",
        agent="inventory",
        tool="search_azure_ai_search",
        query=query,
        enabled=azure_search_enabled(),
    )

    if not azure_search_enabled():
        log_event(
            "azure_search.disabled",
            agent="inventory",
            tool="search_azure_ai_search",
            query=query,
        )
        return "Azure AI Search is not configured. Falling back to local knowledge sources."

    with observe_duration(
        "azure_search.query",
        agent="inventory",
        tool="search_azure_ai_search",
        query=query,
    ):
        results = search_knowledge_chunks(query=query, agent="inventory", top=5)

    log_event(
        "azure_search.results",
        agent="inventory",
        tool="search_azure_ai_search",
        query=query,
        result_count=len(results),
    )

    return format_knowledge_context(results)


INVENTORY_TOOLS = [
    search_azure_ai_search,
    search_inventory_knowledge_base,
    manage_inventory,
    forecast_demand,
    get_reorder_policy_via_mcp,
    send_logistics_response,
    save_agent_memory,
    search_agent_memory,
]

llm = get_chat_llm(temperature=0.0)
inventory_llm = llm.bind_tools(INVENTORY_TOOLS)


def to_langchain_message(message) -> BaseMessage:
    if message.type == "human":
        return HumanMessage(content=message.content)
    if message.type == "ai":
        return AIMessage(content=message.content)
    if message.type == "system":
        return SystemMessage(content=message.content)
    return HumanMessage(content=message.content)


@app.post("/invoke")
def invoke_inventory_agent(request: InventoryRequest, _: None = Depends(require_auth)):
    trace_id = request.trace_id or new_trace_id()
    operation = request.operation or {
        "operation_id": "UNKNOWN",
        "type": "inventory",
        "priority": "medium",
        "status": "active",
    }

    log_event(
        "agent.inventory.request",
        trace_id=trace_id,
        operation=operation,
        message_count=len(request.messages),
    )

    history = [to_langchain_message(m) for m in request.messages]

    latest_user_message = get_latest_human_message(history)

    if is_memory_save_request(latest_user_message):
        memory_key = build_memory_key(latest_user_message)
        memory_value = build_memory_value(latest_user_message)
        memory_id = save_memory(
            key=memory_key,
            value=memory_value,
            memory_type=infer_memory_type(latest_user_message),
            source_agent="inventory",
        )

        log_event(
            "memory.save.auto",
            trace_id=trace_id,
            agent="inventory",
            key=memory_key,
            memory_id=memory_id,
            value_preview=latest_user_message[:300],
        )

        confirmation = (
            f"Long-term memory saved successfully. "
            f"I will remember this for future inventory conversations: {latest_user_message}"
        )

        log_event(
            "agent.inventory.response",
            trace_id=trace_id,
            response_preview=confirmation[:500],
        )

        return {
            "agent": "inventory",
            "response": confirmation,
            "messages": [{"type": "ai", "content": confirmation}],
            "trace_id": trace_id,
        }

    long_term_memory_context = build_long_term_memory_context(latest_user_message)

    inventory_prompt = (
        "You are an inventory and warehouse management specialist.\n"
        "You can use four categories of capabilities:\n"
        "1) Long-term memory: use saved memories for previously stored facts, preferences, suppliers, and operational context.\n"
        "2) Vector RAG knowledge base: use search_inventory_knowledge_base for general policies, thresholds, warehouse rules, and internal guidance.\n"
        "3) MCP tools: use get_reorder_policy_via_mcp for SKU-specific reorder policy from the operational MCP server.\n"
        "4) Local tools: use operational tools like manage_inventory, forecast_demand, and send_logistics_response when action is needed.\n\n"
        "Priority rules:\n"
        "- Long-term memory has priority for user-stored facts, preferences, preferred suppliers, customers, orders, and product-specific facts.\n"
        "- If LONG_TERM_MEMORY_CONTEXT contains a relevant answer, use it directly and mention it as saved long-term memory.\n"
        "- Before saying that supplier, customer, order, product, or SKU-specific information is unavailable, check LONG_TERM_MEMORY_CONTEXT carefully.\n"
        "- If the user asks you to remember, store, save, register, or keep an important fact for the future, save it as long-term memory.\n"
        "- If the user asks about a preferred supplier or previously stored preference, use long-term memory before MCP or RAG.\n"
        "- If the user asks about general policy, thresholds, replenishment rules, backorder handling, or warehouse guidance, call search_inventory_knowledge_base first.\n"
        "- If the user asks about a specific SKU reorder policy, call get_reorder_policy_via_mcp with that SKU.\n"
        "- If long-term memory conflicts with MCP/RAG, clearly distinguish saved user memory from operational policy data.\n"
        "- Do not ask follow-up questions if the SKU/product code is already present in the user message.\n"
        "- After using tools, provide a clear operational recommendation.\n\n"
        f"LONG_TERM_MEMORY_CONTEXT:\n{long_term_memory_context}\n\n"
        f"OPERATION: {json.dumps(operation, ensure_ascii=False)}"
    )

    full = [SystemMessage(content=inventory_prompt)] + history

    with observe_duration(
        "llm.inventory.first_call",
        trace_id=trace_id,
        agent="inventory",
        model=ACTIVE_CHAT_MODEL,
    ):
        first = inventory_llm.invoke(full)

    log_llm_usage(
        "llm.inventory.first_call.usage",
        response=first,
        trace_id=trace_id,
        agent="inventory",
        model=ACTIVE_CHAT_MODEL,
    )

    messages: list[BaseMessage] = [first]

    if getattr(first, "tool_calls", None):
        for tc in first.tool_calls:
            log_event(
                "agent.inventory.tool_call",
                trace_id=trace_id,
                tool=tc["name"],
                args=tc["args"],
            )
            print(
                f"[A2A][Inventory Agent Server] Tool requested: "
                f"{tc['name']} args={tc['args']}"
            )

            fn = next(t for t in INVENTORY_TOOLS if t.name == tc["name"])

            with observe_duration(
                "tool.inventory.invoke",
                trace_id=trace_id,
                agent="inventory",
                tool=tc["name"],
            ):
                out = fn.invoke(tc["args"])

            messages.append(ToolMessage(content=str(out), tool_call_id=tc["id"]))

        with observe_duration(
            "llm.inventory.second_call",
            trace_id=trace_id,
            agent="inventory",
            model=ACTIVE_CHAT_MODEL,
        ):
            second = inventory_llm.invoke(full + messages)

        log_llm_usage(
            "llm.inventory.second_call.usage",
            response=second,
            trace_id=trace_id,
            agent="inventory",
            model=ACTIVE_CHAT_MODEL,
        )

        messages.append(second)

    final_message = messages[-1]

    log_event(
        "agent.inventory.response",
        trace_id=trace_id,
        response_preview=final_message.content[:500],
    )

    return {
        "agent": "inventory",
        "response": final_message.content,
        "messages": [{"type": m.type, "content": m.content} for m in messages],
        "trace_id": trace_id,
    }



@app.get("/products/{code}")
def get_product(code: str, _: None = Depends(require_auth)):
    """Return structured product information for OpenAPI tool calling.

    This endpoint is intentionally simple so external agents can call it as a
    deterministic tool. It returns catalog information plus the inventory policy
    derived from the product ABC class.
    """
    endpoint = "/products/{code}"
    start = time.perf_counter()
    normalized_code = normalize_product_code(code)

    try:
        product = get_product_or_404(code)
        payload = {
            "agent": "inventory",
            "product": build_product_payload(product),
        }

        latency_ms = (time.perf_counter() - start) * 1000
        log_openapi_tool_call(
            endpoint=endpoint,
            status="success",
            http_status_code=200,
            latency_ms=latency_ms,
            product_code=product["code"],
            tool_operation="getProduct",
        )

        return payload
    except HTTPException as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        log_openapi_tool_call(
            endpoint=endpoint,
            status="error",
            http_status_code=exc.status_code,
            latency_ms=latency_ms,
            product_code=normalized_code,
            tool_operation="getProduct",
            error_message=str(exc.detail),
        )
        raise


@app.get("/inventory-policy/{code}")
def get_inventory_policy(code: str, _: None = Depends(require_auth)):
    """Return the inventory policy for a product code.

    The policy is derived by combining the product's ABC class with the ABC
    inventory policy table. This shape is easier for Azure AI Foundry OpenAPI
    tools than the conversational /invoke payload.
    """
    endpoint = "/inventory-policy/{code}"
    start = time.perf_counter()
    normalized_code = normalize_product_code(code)

    try:
        product = get_product_or_404(code)
        abc_class = product["abc_class"]
        policy = ABC_POLICIES[abc_class]
        payload = {
            "agent": "inventory",
            "product_code": product["code"],
            "abc_class": abc_class,
            "policy": policy,
            "source": product.get("source", "structured_inventory_reference_data"),
        }

        latency_ms = (time.perf_counter() - start) * 1000
        log_openapi_tool_call(
            endpoint=endpoint,
            status="success",
            http_status_code=200,
            latency_ms=latency_ms,
            product_code=product["code"],
            abc_class=abc_class,
            tool_operation="getInventoryPolicy",
        )

        return payload
    except HTTPException as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        log_openapi_tool_call(
            endpoint=endpoint,
            status="error",
            http_status_code=exc.status_code,
            latency_ms=latency_ms,
            product_code=normalized_code,
            tool_operation="getInventoryPolicy",
            error_message=str(exc.detail),
        )
        raise


@app.get("/suppliers/{supplier_name}/products")
def get_products_by_supplier(supplier_name: str, _: None = Depends(require_auth)):
    """Return products associated with a preferred supplier."""
    endpoint = "/suppliers/{supplier_name}/products"
    start = time.perf_counter()
    normalized_supplier = supplier_name.strip().lower()

    try:
        products = [
            build_product_payload(product)
            for product in PRODUCT_CATALOG.values()
            if product["preferred_supplier"].lower() == normalized_supplier
        ]

        if not products:
            raise HTTPException(
                status_code=404,
                detail=f"No products found for supplier: {supplier_name}",
            )

        payload = {
            "agent": "inventory",
            "supplier": supplier_name,
            "products": products,
            "source": "structured_inventory_reference_data",
        }

        latency_ms = (time.perf_counter() - start) * 1000
        log_openapi_tool_call(
            endpoint=endpoint,
            status="success",
            http_status_code=200,
            latency_ms=latency_ms,
            supplier_name=supplier_name,
            result_count=len(products),
            tool_operation="getProductsBySupplier",
        )

        return payload
    except HTTPException as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        log_openapi_tool_call(
            endpoint=endpoint,
            status="error",
            http_status_code=exc.status_code,
            latency_ms=latency_ms,
            supplier_name=supplier_name,
            result_count=0,
            tool_operation="getProductsBySupplier",
            error_message=str(exc.detail),
        )
        raise


@app.get("/purchasing-policy")
def get_purchasing_policy(_: None = Depends(require_auth)):
    """Return structured purchasing policy information."""
    endpoint = "/purchasing-policy"
    start = time.perf_counter()

    payload = {
        "agent": "inventory",
        "policy": PURCHASING_POLICY,
        "source": "structured_inventory_reference_data",
    }

    latency_ms = (time.perf_counter() - start) * 1000
    log_openapi_tool_call(
        endpoint=endpoint,
        status="success",
        http_status_code=200,
        latency_ms=latency_ms,
        tool_operation="getPurchasingPolicy",
    )

    return payload




@app.post("/knowledge/search")
def knowledge_search(request: KnowledgeSearchRequest, _: None = Depends(require_auth)):
    """Search document chunks for inventory-related policies and guidance."""
    start = time.perf_counter()
    result = answer_from_knowledge(
        question=request.question,
        agent="inventory",
        top=request.top,
    )
    log_event(
        "api.knowledge.search",
        agent="inventory",
        endpoint="/knowledge/search",
        status="success",
        result_count=result["result_count"],
        latency_ms=round((time.perf_counter() - start) * 1000, 2),
        question_preview=request.question[:300],
    )
    return {"agent": "inventory", **result}

@app.get("/data-source-status")
def data_source_status(_: None = Depends(require_auth)):
    return {
        "agent": "inventory",
        "structured_data_source": "azure_ai_search" if azure_search_enabled() else "local_reference_data",
        "local_fallback_enabled": True,
        "azure_ai_search": azure_search_status(),
    }


@app.get("/memories")
def memories(limit: int = Query(default=50, ge=1, le=200), _: None = Depends(require_auth)):
    return {
        "agent": "inventory",
        "memories": list_memories(limit=limit),
    }


@app.get("/memories/search")
def memories_search(
    query: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    _: None = Depends(require_auth),
):
    return {
        "agent": "inventory",
        "query": query,
        "memories": search_memories(query=query, limit=limit),
    }


@app.delete("/memories/{memory_id}")
def memories_delete(memory_id: str, _: None = Depends(require_auth)):
    deleted = delete_memory(memory_id)

    log_event(
        "memory.delete",
        agent="inventory",
        memory_id=memory_id,
        deleted=deleted,
    )

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Memory not found: {memory_id}",
        )

    return {
        "agent": "inventory",
        "memory_id": memory_id,
        "deleted": True,
    }


@app.get("/metrics")
def metrics(_: None = Depends(require_auth)):
    return {
        "agent": "inventory",
        "summary": get_metrics_summary(),
        "events": get_recent_events(limit=200),
        "traces": get_trace_index(limit=50),
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "agent": "inventory",
        "rag": "vector-faiss",
        "azure_ai_search": "enabled" if azure_search_enabled() else "disabled",
        "azure_ai_search_status": azure_search_status(),
        "azure_ai_search_endpoint": AZURE_SEARCH_ENDPOINT,
        "azure_ai_search_index": AZURE_SEARCH_INDEX_NAME,
        "mcp": "enabled",
        "observability": "jsonl",
        "cache_backend": cache_backend(),
        "index_path": str(VECTOR_INDEX_PATH),
    }

@app.get("/traces")
def traces(limit: int = 50, _: None = Depends(require_auth)):
    return {
        "traces": get_trace_index(limit=limit),
    }


@app.get("/traces/{trace_id}")
def trace_detail(trace_id: str, limit: int = 500, _: None = Depends(require_auth)):
    return {
        "summary": get_trace_summary(trace_id),
        "events": get_trace_events(trace_id, limit=limit),
    }
