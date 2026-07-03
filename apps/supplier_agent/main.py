from __future__ import annotations

import json
import time
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.messages.tool import ToolMessage
from langchain_core.tools import tool

from shared.config import ACTIVE_CHAT_MODEL, AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_INDEX_NAME
from shared.memory import delete_memory, list_memories, save_memory, search_memories
from shared.observability import (
    get_metrics_summary,
    get_recent_events,
    log_event,
    log_llm_usage,
    new_trace_id,
    observe_duration,
)
from shared.schemas import SupplierRequest
from shared.azure_search import azure_search_enabled, format_search_results, search_supply_chain_docs
from shared.llm import get_chat_llm


app = FastAPI(title="Supplier Agent API")


SUPPLIER_REFERENCE_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "supplier_reference_data.json"


def load_supplier_reference_data() -> dict:
    with SUPPLIER_REFERENCE_DATA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


SUPPLIER_REFERENCE_DATA = load_supplier_reference_data()


def normalize_supplier_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def find_supplier(name: str) -> tuple[str, dict] | tuple[None, None]:
    normalized = normalize_supplier_name(name)
    for supplier_name, supplier in SUPPLIER_REFERENCE_DATA.items():
        aliases = {
            normalize_supplier_name(supplier_name),
            normalize_supplier_name(supplier.get("legal_name", "")),
        }
        if normalized in aliases:
            return supplier_name, supplier
    return None, None


def supplier_summary(name: str, supplier: dict) -> dict:
    return {
        "supplier_name": name,
        "supplier_id": supplier["supplier_id"],
        "legal_name": supplier["legal_name"],
        "city": supplier["city"],
        "state": supplier["state"],
        "country": supplier["country"],
        "rating": supplier["rating"],
        "risk_level": supplier["risk_level"],
        "payment_terms": supplier["payment_terms"],
        "buyer": supplier["buyer"],
        "average_lead_time_days": supplier["average_lead_time_days"],
        "products": supplier["products"],
        "source": "structured_supplier_reference_data",
    }


def log_supplier_api_call(*, endpoint: str, tool_operation: str, status: str, http_status_code: int, start: float, **fields):
    log_event(
        "api.openapi_tool.call",
        agent="supplier",
        endpoint=endpoint,
        method="GET",
        status=status,
        http_status_code=http_status_code,
        latency_ms=round((time.perf_counter() - start) * 1000, 2),
        tool_operation=tool_operation,
        **fields,
    )


def get_latest_human_message(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if message.type == "human":
            return str(message.content)
    return ""


def extract_sku(text: str) -> str | None:
    match = re.search(r"\bSKU[-_ ]?[A-Za-z0-9]+\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(0).replace(" ", "-").replace("_", "-").upper()


def extract_supplier_name(text: str) -> str | None:
    patterns = [
        r"supplier\s+([A-Za-z0-9 &.-]+?)\s+is",
        r"that\s+([A-Za-z0-9 &.-]+?)\s+is\s+the\s+preferred\s+supplier",
        r"preferred\s+supplier\s+for\s+.*?\s+is\s+([A-Za-z0-9 &.-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" .")
    return None


def is_memory_save_request(text: str) -> bool:
    normalized = text.strip().lower()
    memory_triggers = [
        "remember that",
        "remember:",
        "save that",
        "store that",
        "keep in memory",
        "memorize that",
    ]
    return any(trigger in normalized for trigger in memory_triggers)


def build_memory_key(text: str) -> str:
    sku = extract_sku(text)
    normalized = "_".join(text.strip().lower().split())[:80]

    if sku and "supplier" in text.lower():
        return f"preferred_supplier_{sku.lower()}"

    if sku:
        return f"supplier_memory_{sku.lower()}"

    return f"supplier_memory_{normalized}"


def build_long_term_memory_context(user_message: str) -> str:
    if not user_message:
        return "No user message available for memory lookup."

    queries = [user_message]
    sku = extract_sku(user_message)
    if sku:
        queries.append(sku)
        queries.append(sku.lower())

    supplier_name = extract_supplier_name(user_message)
    if supplier_name:
        queries.append(supplier_name)

    collected: list[dict] = []
    seen_ids: set[str] = set()

    for query in queries:
        try:
            memories = search_memories(query=query, limit=5)
        except Exception as exc:
            log_event(
                "memory.context.error",
                agent="supplier",
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
        agent="supplier",
        query=user_message,
        result_count=len(collected),
    )

    if not collected:
        return "No relevant long-term memories found."

    return json.dumps(collected[:5], ensure_ascii=False, indent=2)


@tool
def assess_supplier_risk(supplier_name: str, risk_signal: str | None = None) -> str:
    """Assess supplier risk using the available supplier name and optional risk signal."""
    log_event(
        "tool.start",
        agent="supplier",
        tool="assess_supplier_risk",
        supplier_name=supplier_name,
        risk_signal=risk_signal,
    )

    signal = (risk_signal or "no specific risk signal provided").lower()

    if any(word in signal for word in ["delay", "late", "backorder", "quality", "risk", "shortage"]):
        risk_level = "medium"
        recommendation = "Monitor the supplier closely and prepare an alternate supplier option."
    else:
        risk_level = "low"
        recommendation = "Continue using the supplier while monitoring normal SLA indicators."

    return json.dumps(
        {
            "supplier_name": supplier_name,
            "risk_level": risk_level,
            "risk_signal": signal,
            "recommendation": recommendation,
        },
        ensure_ascii=False,
    )


@tool
def compare_suppliers(primary_supplier: str, alternative_supplier: str) -> str:
    """Compare a primary supplier with an alternative supplier for sourcing decisions."""
    log_event(
        "tool.start",
        agent="supplier",
        tool="compare_suppliers",
        primary_supplier=primary_supplier,
        alternative_supplier=alternative_supplier,
    )

    return json.dumps(
        {
            "primary_supplier": primary_supplier,
            "alternative_supplier": alternative_supplier,
            "comparison": "Use the preferred supplier when SLA and availability are acceptable; keep the alternative supplier as contingency.",
            "recommendation": "Validate lead time, quality history, and contract terms before switching suppliers.",
        },
        ensure_ascii=False,
    )


@tool
def recommend_alternative_supplier(sku: str, current_supplier: str | None = None) -> str:
    """Recommend a supplier contingency plan for a SKU."""
    log_event(
        "tool.start",
        agent="supplier",
        tool="recommend_alternative_supplier",
        sku=sku,
        current_supplier=current_supplier,
    )

    return json.dumps(
        {
            "sku": sku,
            "current_supplier": current_supplier,
            "recommendation": "Keep a qualified alternate supplier ready and validate lead time before emergency replenishment.",
            "next_action": "Review supplier SLA, open purchase orders, and recent delivery performance.",
        },
        ensure_ascii=False,
    )


@tool
def save_supplier_memory(key: str, value: str) -> str:
    """Save an important long-term supplier memory for future supplier conversations."""
    memory_id = save_memory(
        key=key,
        value=value,
        source_agent="supplier",
    )

    log_event(
        "memory.save",
        agent="supplier",
        key=key,
        memory_id=memory_id,
    )

    return f"Memory saved successfully. id={memory_id}"


@tool
def search_supplier_memory(query: str) -> str:
    """Search long-term memories related to suppliers, SKUs, preferences, and sourcing context."""
    memories = search_memories(query=query, limit=5)

    log_event(
        "memory.search",
        agent="supplier",
        query=query,
        result_count=len(memories),
    )

    if not memories:
        return "No relevant long-term supplier memories found."

    return "Relevant long-term supplier memories:\n" + json.dumps(
        memories,
        ensure_ascii=False,
        indent=2,
    )


@tool
def search_azure_ai_search(query: str) -> str:
    """Search Azure AI Search for supplier documents, supplier risk, supplier approvals, SLA, lead time, and sourcing guidance."""
    log_event(
        "azure_search.start",
        agent="supplier",
        tool="search_azure_ai_search",
        query=query,
        enabled=azure_search_enabled(),
    )

    if not azure_search_enabled():
        log_event(
            "azure_search.disabled",
            agent="supplier",
            tool="search_azure_ai_search",
            query=query,
        )
        return "Azure AI Search is not configured. Falling back to supplier tools and memory."

    with observe_duration(
        "azure_search.query",
        agent="supplier",
        tool="search_azure_ai_search",
        query=query,
    ):
        results = search_supply_chain_docs(query=query, agent=None, top=3)

    log_event(
        "azure_search.results",
        agent="supplier",
        tool="search_azure_ai_search",
        query=query,
        result_count=len(results),
    )

    return format_search_results(results)


SUPPLIER_TOOLS = [
    search_azure_ai_search,
    assess_supplier_risk,
    compare_suppliers,
    recommend_alternative_supplier,
    save_supplier_memory,
    search_supplier_memory,
]

llm = get_chat_llm(temperature=0.0)
supplier_llm = llm.bind_tools(SUPPLIER_TOOLS)


def to_langchain_message(message) -> BaseMessage:
    if message.type == "human":
        return HumanMessage(content=message.content)
    if message.type == "ai":
        return AIMessage(content=message.content)
    if message.type == "system":
        return SystemMessage(content=message.content)
    return HumanMessage(content=message.content)


@app.post("/invoke")
def invoke_supplier_agent(request: SupplierRequest):
    trace_id = request.trace_id or new_trace_id()
    operation = request.operation or {
        "operation_id": "UNKNOWN",
        "type": "supplier_management",
        "priority": "medium",
        "status": "active",
    }

    log_event(
        "agent.supplier.request",
        trace_id=trace_id,
        operation=operation,
        message_count=len(request.messages),
    )

    history = [to_langchain_message(m) for m in request.messages]
    latest_user_message = get_latest_human_message(history)

    if is_memory_save_request(latest_user_message):
        memory_key = build_memory_key(latest_user_message)
        memory_id = save_memory(
            key=memory_key,
            value=latest_user_message,
            source_agent="supplier",
        )

        log_event(
            "memory.save.auto",
            trace_id=trace_id,
            agent="supplier",
            key=memory_key,
            memory_id=memory_id,
            value_preview=latest_user_message[:300],
        )

        confirmation = (
            "Long-term supplier memory saved successfully. "
            f"I will remember this for future supplier conversations: {latest_user_message}"
        )

        log_event(
            "agent.supplier.response",
            trace_id=trace_id,
            response_preview=confirmation[:500],
        )

        return {
            "agent": "supplier",
            "response": confirmation,
            "messages": [{"type": "ai", "content": confirmation}],
            "trace_id": trace_id,
        }

    long_term_memory_context = build_long_term_memory_context(latest_user_message)

    supplier_prompt = (
        "You are a supplier management specialist.\n"
        "You help with supplier preferences, supplier risk, lead time, SLA, sourcing recommendations, and alternate supplier planning.\n\n"
        "Priority rules:\n"
        "- Long-term memory has priority for user-stored supplier facts, preferred suppliers, and SKU supplier preferences.\n"
        "- If LONG_TERM_MEMORY_CONTEXT contains a relevant answer, use it directly and mention it as saved long-term memory.\n"
        "- If the user asks you to remember, save, store, or keep an important supplier fact, save it as long-term memory.\n"
        "- If the user asks about supplier risk, use assess_supplier_risk when a supplier is present.\n"
        "- If the user asks to compare suppliers, use compare_suppliers.\n"
        "- If the user asks for an alternate supplier or contingency plan for a SKU, use recommend_alternative_supplier.\n"
        "- If available data is incomplete, provide a practical recommendation and list what data should be checked next.\n"
        "- Keep responses operational, concise, and action-oriented.\n\n"
        f"LONG_TERM_MEMORY_CONTEXT:\n{long_term_memory_context}\n\n"
        f"OPERATION: {json.dumps(operation, ensure_ascii=False)}"
    )

    full = [SystemMessage(content=supplier_prompt)] + history

    with observe_duration(
        "llm.supplier.first_call",
        trace_id=trace_id,
        agent="supplier",
        model=ACTIVE_CHAT_MODEL,
    ):
        first = supplier_llm.invoke(full)

    log_llm_usage(
        "llm.supplier.first_call.usage",
        response=first,
        trace_id=trace_id,
        agent="supplier",
        model=ACTIVE_CHAT_MODEL,
    )

    messages: list[BaseMessage] = [first]

    if getattr(first, "tool_calls", None):
        for tc in first.tool_calls:
            log_event(
                "agent.supplier.tool_call",
                trace_id=trace_id,
                tool=tc["name"],
                args=tc["args"],
            )

            fn = next(t for t in SUPPLIER_TOOLS if t.name == tc["name"])

            with observe_duration(
                "tool.supplier.invoke",
                trace_id=trace_id,
                agent="supplier",
                tool=tc["name"],
            ):
                out = fn.invoke(tc["args"])

            messages.append(ToolMessage(content=str(out), tool_call_id=tc["id"]))

        with observe_duration(
            "llm.supplier.second_call",
            trace_id=trace_id,
            agent="supplier",
            model=ACTIVE_CHAT_MODEL,
        ):
            second = supplier_llm.invoke(full + messages)

        log_llm_usage(
            "llm.supplier.second_call.usage",
            response=second,
            trace_id=trace_id,
            agent="supplier",
            model=ACTIVE_CHAT_MODEL,
        )

        messages.append(second)

    final_message = messages[-1]

    log_event(
        "agent.supplier.response",
        trace_id=trace_id,
        response_preview=final_message.content[:500],
    )

    return {
        "agent": "supplier",
        "response": final_message.content,
        "messages": [{"type": m.type, "content": m.content} for m in messages],
        "trace_id": trace_id,
    }



@app.get("/suppliers")
def get_suppliers():
    start = time.perf_counter()
    suppliers = [
        supplier_summary(name, supplier)
        for name, supplier in SUPPLIER_REFERENCE_DATA.items()
    ]
    log_supplier_api_call(
        endpoint="/suppliers",
        tool_operation="listSuppliers",
        status="success",
        http_status_code=200,
        start=start,
        result_count=len(suppliers),
    )
    return {
        "agent": "supplier",
        "suppliers": suppliers,
    }


@app.get("/suppliers/{supplier_name}")
def get_supplier(supplier_name: str):
    start = time.perf_counter()
    canonical_name, supplier = find_supplier(supplier_name)
    if not supplier:
        log_supplier_api_call(
            endpoint="/suppliers/{supplier_name}",
            tool_operation="getSupplier",
            status="error",
            http_status_code=404,
            start=start,
            supplier_name=supplier_name,
            error_message=f"Supplier not found: {supplier_name}",
        )
        raise HTTPException(status_code=404, detail=f"Supplier not found: {supplier_name}")

    payload = supplier_summary(canonical_name, supplier)
    log_supplier_api_call(
        endpoint="/suppliers/{supplier_name}",
        tool_operation="getSupplier",
        status="success",
        http_status_code=200,
        start=start,
        supplier_name=canonical_name,
        supplier_id=supplier["supplier_id"],
    )
    return {
        "agent": "supplier",
        "supplier": payload,
    }


@app.get("/suppliers/{supplier_name}/products")
def get_supplier_products(supplier_name: str):
    start = time.perf_counter()
    canonical_name, supplier = find_supplier(supplier_name)
    if not supplier:
        log_supplier_api_call(
            endpoint="/suppliers/{supplier_name}/products",
            tool_operation="getSupplierProducts",
            status="error",
            http_status_code=404,
            start=start,
            supplier_name=supplier_name,
            error_message=f"Supplier not found: {supplier_name}",
        )
        raise HTTPException(status_code=404, detail=f"Supplier not found: {supplier_name}")

    products = supplier["products"]
    log_supplier_api_call(
        endpoint="/suppliers/{supplier_name}/products",
        tool_operation="getSupplierProducts",
        status="success",
        http_status_code=200,
        start=start,
        supplier_name=canonical_name,
        supplier_id=supplier["supplier_id"],
        result_count=len(products),
    )
    return {
        "agent": "supplier",
        "supplier_name": canonical_name,
        "products": products,
        "source": "structured_supplier_reference_data",
    }


@app.get("/suppliers/{supplier_name}/contracts")
def get_supplier_contracts(supplier_name: str):
    start = time.perf_counter()
    canonical_name, supplier = find_supplier(supplier_name)
    if not supplier:
        log_supplier_api_call(
            endpoint="/suppliers/{supplier_name}/contracts",
            tool_operation="getSupplierContracts",
            status="error",
            http_status_code=404,
            start=start,
            supplier_name=supplier_name,
            error_message=f"Supplier not found: {supplier_name}",
        )
        raise HTTPException(status_code=404, detail=f"Supplier not found: {supplier_name}")

    contracts = supplier["contracts"]
    log_supplier_api_call(
        endpoint="/suppliers/{supplier_name}/contracts",
        tool_operation="getSupplierContracts",
        status="success",
        http_status_code=200,
        start=start,
        supplier_name=canonical_name,
        supplier_id=supplier["supplier_id"],
        result_count=len(contracts),
    )
    return {
        "agent": "supplier",
        "supplier_name": canonical_name,
        "contracts": contracts,
        "source": "structured_supplier_reference_data",
    }


@app.get("/suppliers/{supplier_name}/performance")
def get_supplier_performance(supplier_name: str):
    start = time.perf_counter()
    canonical_name, supplier = find_supplier(supplier_name)
    if not supplier:
        log_supplier_api_call(
            endpoint="/suppliers/{supplier_name}/performance",
            tool_operation="getSupplierPerformance",
            status="error",
            http_status_code=404,
            start=start,
            supplier_name=supplier_name,
            error_message=f"Supplier not found: {supplier_name}",
        )
        raise HTTPException(status_code=404, detail=f"Supplier not found: {supplier_name}")

    performance = {
        "rating": supplier["rating"],
        "risk_level": supplier["risk_level"],
        "sla_on_time_delivery_percent": supplier["sla_on_time_delivery_percent"],
        "quality_score": supplier["quality_score"],
        "average_lead_time_days": supplier["average_lead_time_days"],
    }
    log_supplier_api_call(
        endpoint="/suppliers/{supplier_name}/performance",
        tool_operation="getSupplierPerformance",
        status="success",
        http_status_code=200,
        start=start,
        supplier_name=canonical_name,
        supplier_id=supplier["supplier_id"],
    )
    return {
        "agent": "supplier",
        "supplier_name": canonical_name,
        "performance": performance,
        "source": "structured_supplier_reference_data",
    }


@app.get("/metrics")
def metrics():
    return {
        "agent": "supplier",
        "summary": get_metrics_summary(),
        "events": get_recent_events(limit=200),
    }


def only_supplier_memories(memories: list[dict]) -> list[dict]:
    return [
        memory
        for memory in memories
        if memory.get("source_agent") == "supplier"
    ]


@app.get("/memories")
def memories():
    all_memories = list_memories(limit=200)
    supplier_memories = only_supplier_memories(all_memories)

    return {
        "agent": "supplier",
        "memories": supplier_memories,
    }


@app.get("/memories/search")
def search_memories_endpoint(query: str, limit: int = 20):
    all_matches = search_memories(query=query, limit=limit)
    supplier_matches = only_supplier_memories(all_matches)

    return {
        "agent": "supplier",
        "query": query,
        "memories": supplier_matches,
    }


@app.delete("/memories/{memory_id}")
def delete_memory_endpoint(memory_id: str):
    supplier_memories = only_supplier_memories(list_memories(limit=500))
    supplier_memory_ids = {str(memory.get("id")) for memory in supplier_memories}

    if memory_id not in supplier_memory_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Supplier memory not found: {memory_id}",
        )

    deleted = delete_memory(memory_id=memory_id)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Memory not found: {memory_id}",
        )

    log_event(
        "memory.delete",
        agent="supplier",
        memory_id=memory_id,
        deleted=deleted,
    )

    return {
        "agent": "supplier",
        "memory_id": memory_id,
        "deleted": deleted,
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "agent": "supplier",
        "observability": "jsonl",
        "memory": "sqlite",
        "azure_ai_search": "enabled" if azure_search_enabled() else "disabled",
        "azure_ai_search_endpoint": AZURE_SEARCH_ENDPOINT,
        "azure_ai_search_index": AZURE_SEARCH_INDEX_NAME,
        "capabilities": [
            "supplier_memory",
            "supplier_risk",
            "supplier_comparison",
            "alternate_supplier_recommendation",
            "supplier_rest_api",
            "supplier_contract_lookup",
            "supplier_performance_lookup",
        ],
    }
