from __future__ import annotations

import json
import operator
import re
import time
from urllib.parse import quote
from typing import Annotated, Literal, Optional, Sequence, TypedDict

import requests
from fastapi import FastAPI
from pydantic import BaseModel

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from shared.config import INVENTORY_AGENT_URL, SUPPLIER_AGENT_URL, ACTIVE_CHAT_MODEL
from shared.llm import get_chat_llm
from shared.azure_search import answer_from_knowledge, azure_search_status
from shared.observability import (
    log_event,
    new_trace_id,
    observe_duration,
    log_llm_usage,
    get_metrics_summary,
    get_recent_events,
)


app = FastAPI(title="Supervisor Agent API")

llm = get_chat_llm(temperature=0.0)


class ChatRequest(BaseModel):
    message: str
    operation: Optional[dict] = None


class ChatResponse(BaseModel):
    response: str
    trace_id: str
    validation_passed: Optional[bool] = None
    validation_reason: Optional[str] = None


class CopilotRequest(BaseModel):
    question: str


class CopilotResponse(BaseModel):
    answer: str
    trace_id: str
    validation_passed: Optional[bool] = None
    validation_reason: Optional[str] = None


class KnowledgeSearchRequest(BaseModel):
    question: str
    agent: Optional[str] = None
    top: int = 5


class KnowledgeAnswerRequest(BaseModel):
    question: str
    agent: Optional[str] = None
    top: int = 5


class KnowledgeAnswerResponse(BaseModel):
    answer: str
    trace_id: str
    result_count: int
    context: str
    sources: list[dict]


class AgentState(TypedDict):
    operation: Optional[dict]
    messages: Annotated[Sequence[BaseMessage], operator.add]
    trace_id: str
    validation_passed: Optional[bool]
    validation_reason: Optional[str]
    validation_attempts: int
    selected_route: Optional[str]


def supervisor_node(state: AgentState):
    history = state["messages"]
    operation = state.get("operation", {})
    operation_json = json.dumps(operation, ensure_ascii=False)
    trace_id = state.get("trace_id") or new_trace_id()

    supervisor_prompt = (
        "You are a supervisor coordinating a team of supply chain specialists.\n"
        "Currently available team members:\n"
        "- inventory: Handles inventory levels, stock, product codes, reorder policy, SKU-specific thresholds and inventory long-term memory.\n"
        "- supplier: Handles supplier risk, supplier comparison, supplier alternatives, lead time, SLA, contracts, buyer, payment terms, vendor reliability and supplier-specific memory.\n"
        "- knowledge: Handles policy, procedure, contract, guidance and document-based questions by retrieving document chunks from Azure AI Search and generating a grounded answer.\n\n"
        "Routing rules:\n"
        "- Route questions about concrete stock/product values, product master data, reorder policy or SKU operations to inventory.\n"
        "- Route questions about concrete supplier profile, risk, supplier comparison, alternate suppliers, SLA, lead time, contracts, buyer, payment terms or vendor recommendations to supplier.\n"
        "- Route hybrid questions that require both product/inventory data and supplier/vendor data to both.\n"
        "- Route open-ended questions asking what a policy, procedure, contract, document or internal guidance says to knowledge.\n"
        "- If the user asks to remember a preferred supplier for a SKU, route to inventory for now, because preferred supplier memory is currently stored in the inventory memory layer.\n\n"
        "Based on the user query, select the best route.\n"
        "Output ONLY one of these names: inventory, supplier, both, or knowledge. Do not output anything else.\n\n"
        f"OPERATION: {operation_json}"
    )

    log_event("agent.supervisor.start", trace_id=trace_id, operation=operation)

    with observe_duration(
        "llm.supervisor.route",
        trace_id=trace_id,
        agent="supervisor",
    ):
        response = llm.invoke([SystemMessage(content=supervisor_prompt)] + history)

    log_llm_usage(
        "llm.supervisor.usage",
        response=response,
        trace_id=trace_id,
        agent="supervisor",
        model=ACTIVE_CHAT_MODEL,
    )

    route = response.content.strip().lower()
    log_event("supervisor.route.selected", trace_id=trace_id, route=route)

    return {
        "messages": [AIMessage(content=route)],
        "trace_id": trace_id,
        "validation_attempts": state.get("validation_attempts", 0),
        "selected_route": route,
    }


def build_a2a_payload(state: AgentState, trace_id: str) -> dict:
    return {
        "operation": state.get("operation", {}),
        "trace_id": trace_id,
        "messages": [
            {"type": m.type, "content": m.content}
            for m in state["messages"]
            if m.type in ["human", "ai", "system"]
        ],
    }


def call_specialist_agent(
    *,
    state: AgentState,
    trace_id: str,
    target_agent: str,
    target_url: str,
    timeout_seconds: int = 60,
) -> str:
    payload = build_a2a_payload(state, trace_id)

    log_event(
        "a2a.request",
        trace_id=trace_id,
        target=target_agent,
        url=target_url,
    )
    log_event(
        f"supervisor.agent_call.{target_agent}.start",
        trace_id=trace_id,
        target_agent=target_agent,
        url=target_url,
    )

    with observe_duration(
        f"a2a.{target_agent}.request",
        trace_id=trace_id,
        agent="supervisor",
        target_agent=target_agent,
        url=target_url,
    ):
        last_exception = None
        response = None

        for attempt in range(1, 3):
            try:
                log_event(
                    f"a2a.{target_agent}.attempt",
                    trace_id=trace_id,
                    target=target_agent,
                    attempt=attempt,
                )

                response = requests.post(
                    target_url,
                    json=payload,
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
                last_exception = None
                break

            except requests.exceptions.RequestException as exc:
                last_exception = exc

                log_event(
                    f"a2a.{target_agent}.retry",
                    trace_id=trace_id,
                    target=target_agent,
                    attempt=attempt,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )

                if attempt < 2:
                    time.sleep(1)

        if last_exception is not None:
            raise last_exception

        if response is None:
            raise RuntimeError(f"{target_agent} Agent did not return a response.")

    data = response.json()
    specialist_response = data.get("response")

    if not specialist_response:
        raise ValueError(f"{target_agent} Agent response did not include a 'response' field.")

    log_event(
        "a2a.response",
        trace_id=trace_id,
        target=target_agent,
        response_preview=specialist_response[:500],
    )
    log_event(
        f"supervisor.agent_call.{target_agent}.success",
        trace_id=trace_id,
        target_agent=target_agent,
        response_preview=specialist_response[:500],
    )

    return specialist_response


def inventory_node(state: AgentState):
    trace_id = state.get("trace_id") or new_trace_id()

    try:
        inventory_response = call_specialist_agent(
            state=state,
            trace_id=trace_id,
            target_agent="inventory",
            target_url=INVENTORY_AGENT_URL,
        )

        return {
            "messages": [AIMessage(content=inventory_response)],
            "trace_id": trace_id,
        }

    except requests.exceptions.Timeout as exc:
        log_event(
            "a2a.inventory.error",
            trace_id=trace_id,
            target="inventory",
            error_type=type(exc).__name__,
            error_message=str(exc),
            fallback="inventory_timeout_message",
        )

        fallback_message = (
            "I could not reach the Inventory Agent within the expected time. "
            "Please try again in a moment. If the issue persists, check whether the "
            "Inventory Agent is running and healthy."
        )

        return {"messages": [AIMessage(content=fallback_message)], "trace_id": trace_id}

    except requests.exceptions.RequestException as exc:
        log_event(
            "a2a.inventory.error",
            trace_id=trace_id,
            target="inventory",
            error_type=type(exc).__name__,
            error_message=str(exc),
            fallback="inventory_unavailable_message",
        )

        fallback_message = (
            "The Inventory Agent is currently unavailable. "
            "I could not complete the inventory analysis, but the request was tracked "
            f"with trace_id {trace_id}."
        )

        return {"messages": [AIMessage(content=fallback_message)], "trace_id": trace_id}

    except Exception as exc:
        log_event(
            "a2a.inventory.error",
            trace_id=trace_id,
            target="inventory",
            error_type=type(exc).__name__,
            error_message=str(exc),
            fallback="inventory_unexpected_error_message",
        )

        fallback_message = (
            "An unexpected error occurred while processing the inventory request. "
            f"The request was tracked with trace_id {trace_id}."
        )

        return {"messages": [AIMessage(content=fallback_message)], "trace_id": trace_id}


def supplier_node(state: AgentState):
    trace_id = state.get("trace_id") or new_trace_id()

    try:
        supplier_response = call_specialist_agent(
            state=state,
            trace_id=trace_id,
            target_agent="supplier",
            target_url=SUPPLIER_AGENT_URL,
        )

        return {
            "messages": [AIMessage(content=supplier_response)],
            "trace_id": trace_id,
        }

    except requests.exceptions.Timeout as exc:
        log_event(
            "a2a.supplier.error",
            trace_id=trace_id,
            target="supplier",
            error_type=type(exc).__name__,
            error_message=str(exc),
            fallback="supplier_timeout_message",
        )

        fallback_message = (
            "I could not reach the Supplier Agent within the expected time. "
            "Please try again in a moment. If the issue persists, check whether the "
            "Supplier Agent is running and healthy."
        )

        return {"messages": [AIMessage(content=fallback_message)], "trace_id": trace_id}

    except requests.exceptions.RequestException as exc:
        log_event(
            "a2a.supplier.error",
            trace_id=trace_id,
            target="supplier",
            error_type=type(exc).__name__,
            error_message=str(exc),
            fallback="supplier_unavailable_message",
        )

        fallback_message = (
            "The Supplier Agent is currently unavailable. "
            "I could not complete the supplier analysis, but the request was tracked "
            f"with trace_id {trace_id}."
        )

        return {"messages": [AIMessage(content=fallback_message)], "trace_id": trace_id}

    except Exception as exc:
        log_event(
            "a2a.supplier.error",
            trace_id=trace_id,
            target="supplier",
            error_type=type(exc).__name__,
            error_message=str(exc),
            fallback="supplier_unexpected_error_message",
        )

        fallback_message = (
            "An unexpected error occurred while processing the supplier request. "
            f"The request was tracked with trace_id {trace_id}."
        )

        return {"messages": [AIMessage(content=fallback_message)], "trace_id": trace_id}



def _service_base_url(agent_url: str) -> str:
    """Return the base URL for a specialist service from its conversational endpoint."""
    clean_url = agent_url.rstrip("/")
    for suffix in ("/invoke", "/copilot", "/chat"):
        if clean_url.endswith(suffix):
            return clean_url[: -len(suffix)]
    return clean_url


def _extract_product_code(text: str) -> Optional[str]:
    """Extract a product/SKU-like code from a natural-language question."""
    match = re.search(r"\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9]+-[A-Z0-9]+\b", text.upper())
    return match.group(0) if match else None


def _structured_get(
    *,
    base_url: str,
    path: str,
    trace_id: str,
    tool_operation: str,
    timeout_seconds: int = 30,
) -> dict:
    """Call a deterministic REST endpoint and return JSON data."""
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    log_event(
        "supervisor.structured_tool.request",
        trace_id=trace_id,
        url=url,
        tool_operation=tool_operation,
    )

    start = time.perf_counter()
    response = requests.get(url, timeout=timeout_seconds)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    log_event(
        "supervisor.structured_tool.response",
        trace_id=trace_id,
        url=url,
        tool_operation=tool_operation,
        http_status_code=response.status_code,
        latency_ms=latency_ms,
    )

    response.raise_for_status()
    return response.json()


def _format_inventory_policy(policy: dict) -> str:
    if not policy:
        return "política de estoque não encontrada"

    parts = []
    if policy.get("safety_stock_units") is not None:
        parts.append(f"estoque de segurança de {policy['safety_stock_units']} unidades")
    if policy.get("critical_level_units") is not None:
        parts.append(f"nível crítico de {policy['critical_level_units']} unidades")
    if policy.get("replenishment_frequency"):
        parts.append(f"reposição {policy['replenishment_frequency']}")
    if policy.get("review_frequency"):
        parts.append(f"revisão {policy['review_frequency']}")

    return ", ".join(parts) if parts else "política de estoque não encontrada"


def multi_agent_node(state: AgentState):
    """Answer hybrid questions using deterministic REST endpoints from both specialists."""
    trace_id = state.get("trace_id") or new_trace_id()
    user_messages = [m.content for m in state["messages"] if m.type == "human"]
    user_question = user_messages[-1] if user_messages else ""
    product_code = _extract_product_code(user_question)

    log_event(
        "supervisor.multi_agent.start",
        trace_id=trace_id,
        route="both",
        question_preview=user_question[:500],
        product_code=product_code,
        strategy="structured_rest",
    )

    if not product_code:
        fallback_message = (
            "Não consegui identificar um código de produto na pergunta. "
            "Informe um código como PARAFUSO-M20 para que eu consulte estoque e fornecedor."
        )
        log_event(
            "supervisor.multi_agent.missing_product_code",
            trace_id=trace_id,
            question_preview=user_question[:500],
        )
        return {
            "messages": [AIMessage(content=fallback_message)],
            "trace_id": trace_id,
            "selected_route": "both",
        }

    inventory_base_url = _service_base_url(INVENTORY_AGENT_URL)
    supplier_base_url = _service_base_url(SUPPLIER_AGENT_URL)

    product_payload: dict = {}
    policy_payload: dict = {}
    supplier_payload: dict = {}
    errors: list[str] = []

    try:
        product_payload = _structured_get(
            base_url=inventory_base_url,
            path=f"/products/{quote(product_code)}",
            trace_id=trace_id,
            tool_operation="getProduct",
        )
    except Exception as exc:
        errors.append(f"produto {product_code}: {exc}")
        log_event(
            "supervisor.multi_agent.structured_inventory_product.error",
            trace_id=trace_id,
            product_code=product_code,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    try:
        policy_payload = _structured_get(
            base_url=inventory_base_url,
            path=f"/inventory-policy/{quote(product_code)}",
            trace_id=trace_id,
            tool_operation="getInventoryPolicy",
        )
    except Exception as exc:
        errors.append(f"política de estoque {product_code}: {exc}")
        log_event(
            "supervisor.multi_agent.structured_inventory_policy.error",
            trace_id=trace_id,
            product_code=product_code,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    product = product_payload.get("product", {}) if isinstance(product_payload, dict) else {}
    supplier_name = product.get("preferred_supplier")

    if supplier_name:
        try:
            supplier_payload = _structured_get(
                base_url=supplier_base_url,
                path=f"/suppliers/{quote(supplier_name)}",
                trace_id=trace_id,
                tool_operation="getSupplier",
            )
        except Exception as exc:
            errors.append(f"fornecedor {supplier_name}: {exc}")
            log_event(
                "supervisor.multi_agent.structured_supplier.error",
                trace_id=trace_id,
                supplier_name=supplier_name,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    policy = product.get("inventory_policy") or policy_payload.get("inventory_policy", {})
    supplier = supplier_payload.get("supplier", {}) if isinstance(supplier_payload, dict) else {}

    if not product and not policy_payload:
        answer = (
            f"Não encontrei dados estruturados para o produto {product_code}. "
            "Não foi possível consolidar fornecedor e política de estoque."
        )
    else:
        answer_lines = [f"O produto {product_code} é fornecido por {supplier_name or 'fornecedor não encontrado'}."]

        if supplier:
            supplier_details = []
            if supplier.get("rating"):
                supplier_details.append(f"rating {supplier['rating']}")
            if supplier.get("risk_level"):
                supplier_details.append(f"risco {supplier['risk_level']}")
            if supplier.get("city") and supplier.get("state"):
                supplier_details.append(f"localização {supplier['city']}/{supplier['state']}")
            if supplier_details:
                answer_lines.append("Dados do fornecedor: " + ", ".join(supplier_details) + ".")

        abc_class = product.get("abc_class") or policy_payload.get("abc_class")
        policy_text = _format_inventory_policy(policy)
        if abc_class:
            answer_lines.append(f"A classe ABC do item é {abc_class}. A política de estoque define {policy_text}.")
        else:
            answer_lines.append(f"A política de estoque define {policy_text}.")

        if errors:
            answer_lines.append("Alguns dados complementares não foram encontrados: " + "; ".join(errors) + ".")

        answer = " ".join(answer_lines)

    log_event(
        "supervisor.multi_agent.response",
        trace_id=trace_id,
        product_code=product_code,
        supplier_name=supplier_name,
        strategy="structured_rest",
        response_preview=answer[:500],
    )

    return {
        "messages": [AIMessage(content=answer)],
        "trace_id": trace_id,
        "selected_route": "both",
    }



def build_grounded_knowledge_answer(
    *,
    question: str,
    trace_id: str,
    agent: str | None = None,
    top: int = 5,
) -> dict:
    """Retrieve document chunks and generate a grounded answer in Portuguese."""
    start = time.perf_counter()
    rag_result = answer_from_knowledge(question=question, agent=agent, top=top)
    context = rag_result["context"]
    results = rag_result["results"]

    sources = [
        {
            "title": item.get("title"),
            "source": item.get("source"),
            "agent": item.get("agent"),
            "score": item.get("@search.score"),
        }
        for item in results
    ]

    if not results:
        answer = (
            "Não encontrei trechos relevantes na base documental do Azure AI Search "
            "para responder a essa pergunta com segurança."
        )
    else:
        generation_prompt = (
            "Você é um supervisor de supply chain respondendo com base em documentos corporativos.\n"
            "Responda em português do Brasil.\n"
            "Use somente as informações do CONTEXTO recuperado do Azure AI Search.\n"
            "Não invente dados, números, fornecedores, contratos ou políticas.\n"
            "Se a resposta estiver parcialmente disponível, deixe claro o que foi encontrado e o que não foi encontrado.\n"
            "No final, inclua uma seção curta chamada 'Fontes consultadas' com os títulos dos chunks usados.\n\n"
            f"PERGUNTA DO USUÁRIO:\n{question}\n\n"
            f"CONTEXTO:\n{context}\n"
        )

        with observe_duration(
            "llm.supervisor.knowledge_answer",
            trace_id=trace_id,
            agent="supervisor",
        ):
            response = llm.invoke([SystemMessage(content=generation_prompt)])

        log_llm_usage(
            "llm.supervisor.knowledge_answer.usage",
            response=response,
            trace_id=trace_id,
            agent="supervisor",
            model=ACTIVE_CHAT_MODEL,
        )
        answer = response.content

    log_event(
        "supervisor.knowledge.answer",
        trace_id=trace_id,
        result_count=rag_result["result_count"],
        agent_filter=agent,
        latency_ms=round((time.perf_counter() - start) * 1000, 2),
        question_preview=question[:300],
        answer_preview=answer[:500],
    )

    return {
        "answer": answer,
        "trace_id": trace_id,
        "result_count": rag_result["result_count"],
        "context": context,
        "sources": sources,
        "results": results,
    }


def knowledge_node(state: AgentState):
    """Answer document-grounded policy/procedure/contract questions using Azure AI Search + LLM."""
    trace_id = state.get("trace_id") or new_trace_id()
    user_messages = [m.content for m in state["messages"] if m.type == "human"]
    user_question = user_messages[-1] if user_messages else ""

    result = build_grounded_knowledge_answer(
        question=user_question,
        trace_id=trace_id,
        agent=None,
        top=5,
    )

    return {
        "messages": [AIMessage(content=result["answer"])],
        "trace_id": trace_id,
        "selected_route": "knowledge",
    }

def validator_node(state: AgentState):
    trace_id = state.get("trace_id") or new_trace_id()
    user_messages = [m.content for m in state["messages"] if m.type == "human"]
    latest_answer = state["messages"][-1].content

    validation_prompt = (
        "You are a strict validator for a supply chain agent response.\n"
        "Evaluate whether the answer is acceptable.\n\n"
        "Criteria:\n"
        "1. The answer must address the user's question.\n"
        "2. If the question is about policy, thresholds, backorders, warehouse rules, or internal guidance, the answer should mention policy-based guidance.\n"
        "3. If the question is about supplier risk, supplier comparison, alternate suppliers, SLA, vendor reliability, or procurement supplier recommendations, the answer should provide supplier-focused guidance.\n"
        "4. The answer should contain a clear recommendation or next action when appropriate.\n"
        "5. The answer must not invent precise data that was not retrieved or provided.\n"
        "6. If a specialist agent explicitly states that information came from long-term memory, treat that as retrieved/provided data.\n\n"
        "Return JSON only in this format:\n"
        "{\"passed\": true/false, \"reason\": \"short reason\"}\n\n"
        f"USER QUESTION: {user_messages[-1] if user_messages else ''}\n\n"
        f"ANSWER: {latest_answer}"
    )

    with observe_duration(
        "llm.validator.check",
        trace_id=trace_id,
        agent="validator",
    ):
        response = llm.invoke([SystemMessage(content=validation_prompt)])

    log_llm_usage(
        "llm.validator.usage",
        response=response,
        trace_id=trace_id,
        agent="validator",
        model=ACTIVE_CHAT_MODEL,
    )

    try:
        parsed = json.loads(response.content)
        passed = bool(parsed.get("passed"))
        reason = str(parsed.get("reason", "No reason provided."))
    except Exception:
        passed = False
        reason = f"Validator returned non-JSON: {response.content}"

    attempts = state.get("validation_attempts", 0) + 1

    log_event(
        "agent.validator.result",
        trace_id=trace_id,
        passed=passed,
        reason=reason,
        attempts=attempts,
    )

    return {
        "validation_passed": passed,
        "validation_reason": reason,
        "validation_attempts": attempts,
        "trace_id": trace_id,
    }


def improve_response_node(state: AgentState):
    trace_id = state.get("trace_id") or new_trace_id()
    user_messages = [m.content for m in state["messages"] if m.type == "human"]
    latest_answer = state["messages"][-1].content
    reason = state.get("validation_reason") or "No reason provided."

    improve_prompt = (
        "Improve the supply chain response using the validator feedback.\n"
        "Do not claim new data was retrieved. Use only the existing answer and user question.\n"
        "Make the response clearer, more actionable, and more grounded.\n\n"
        f"USER QUESTION: {user_messages[-1] if user_messages else ''}\n\n"
        f"CURRENT ANSWER: {latest_answer}\n\n"
        f"VALIDATOR FEEDBACK: {reason}"
    )

    with observe_duration(
        "llm.improve_response",
        trace_id=trace_id,
        agent="supervisor",
    ):
        response = llm.invoke([SystemMessage(content=improve_prompt)])

    log_llm_usage(
        "llm.improve_response.usage",
        response=response,
        trace_id=trace_id,
        agent="supervisor",
        model=ACTIVE_CHAT_MODEL,
    )

    log_event(
        "agent.improve_response",
        trace_id=trace_id,
        reason=reason,
        improved_preview=response.content[:500],
    )

    return {"messages": [AIMessage(content=response.content)], "trace_id": trace_id}


def route_to_specialist(state: AgentState) -> Literal["inventory", "supplier", "both", "knowledge"]:
    agent_name = state["messages"][-1].content.strip().lower()
    trace_id = state.get("trace_id") or new_trace_id()

    if agent_name == "inventory":
        return "inventory"

    if agent_name == "supplier":
        return "supplier"

    if agent_name == "both":
        return "both"

    if agent_name == "knowledge":
        return "knowledge"

    log_event(
        "agent.supervisor.route_fallback",
        trace_id=trace_id,
        requested_route=agent_name,
        fallback_route="inventory",
        reason="Unknown route. Falling back to inventory specialist.",
    )

    return "inventory"


def route_after_validation(state: AgentState) -> Literal["end", "improve"]:
    latest_answer = state["messages"][-1].content.lower()

    if state.get("selected_route") in {"both", "knowledge"}:
        log_event(
            "agent.validator.bypass",
            trace_id=state.get("trace_id") or new_trace_id(),
            reason="Structured hybrid or document-grounded RAG response should not be rewritten by improve_response.",
        )
        return "end"

    if "long-term memory" in latest_answer:
        log_event(
            "agent.validator.bypass",
            trace_id=state.get("trace_id") or new_trace_id(),
            reason="Specialist answer was grounded in long-term memory.",
        )
        return "end"

    if state.get("validation_passed"):
        return "end"

    return "improve"


def construct_graph():
    g = StateGraph(AgentState)

    g.add_node("supervisor", supervisor_node)
    g.add_node("inventory", inventory_node)
    g.add_node("supplier", supplier_node)
    g.add_node("both", multi_agent_node)
    g.add_node("knowledge", knowledge_node)
    g.add_node("validator", validator_node)
    g.add_node("improve_response", improve_response_node)

    g.set_entry_point("supervisor")

    g.add_conditional_edges(
        "supervisor",
        route_to_specialist,
        {"inventory": "inventory", "supplier": "supplier", "both": "both", "knowledge": "knowledge"},
    )
    g.add_edge("inventory", "validator")
    g.add_edge("supplier", "validator")
    g.add_edge("both", "validator")
    g.add_edge("knowledge", "validator")
    g.add_conditional_edges(
        "validator",
        route_after_validation,
        {"end": END, "improve": "improve_response"},
    )
    g.add_edge("improve_response", END)

    return g.compile()


graph = construct_graph()


@app.post("/knowledge/search")
def knowledge_search(request: KnowledgeSearchRequest):
    """Search document chunks across Azure AI Search for supervisor-level RAG."""
    trace_id = new_trace_id()
    start = time.perf_counter()
    result = answer_from_knowledge(
        question=request.question,
        agent=request.agent,
        top=request.top,
    )
    log_event(
        "api.knowledge.search",
        agent="supervisor",
        endpoint="/knowledge/search",
        trace_id=trace_id,
        status="success",
        agent_filter=request.agent,
        result_count=result["result_count"],
        latency_ms=round((time.perf_counter() - start) * 1000, 2),
        question_preview=request.question[:300],
    )
    return {"agent": "supervisor", "trace_id": trace_id, "azure_search": azure_search_status(), **result}


@app.post("/knowledge/answer", response_model=KnowledgeAnswerResponse)
def knowledge_answer(request: KnowledgeAnswerRequest):
    """Retrieve document chunks and generate a final grounded answer."""
    trace_id = new_trace_id()
    result = build_grounded_knowledge_answer(
        question=request.question,
        trace_id=trace_id,
        agent=request.agent,
        top=request.top,
    )
    return KnowledgeAnswerResponse(
        answer=result["answer"],
        trace_id=trace_id,
        result_count=result["result_count"],
        context=result["context"],
        sources=result["sources"],
    )


@app.get("/metrics")
def metrics():
    return {
        "agent": "supervisor",
        "summary": get_metrics_summary(),
        "events": get_recent_events(limit=200),
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "agent": "supervisor",
        "routes": ["inventory", "supplier", "both", "knowledge"],
    }


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    trace_id = new_trace_id()

    operation = request.operation or {
        "operation_id": "OP-12345",
        "type": "supply_chain_management",
        "priority": "high",
        "location": "Warehouse A",
    }

    with observe_duration(
        "api.chat.request",
        trace_id=trace_id,
        agent="supervisor",
        endpoint="/chat",
    ):
        result = graph.invoke(
            {
                "operation": operation,
                "messages": [HumanMessage(content=request.message)],
                "trace_id": trace_id,
                "validation_passed": None,
                "validation_reason": None,
                "validation_attempts": 0,
                "selected_route": None,
            }
        )

    final_message = result["messages"][-1].content

    return ChatResponse(
        response=final_message,
        trace_id=trace_id,
        validation_passed=result.get("validation_passed"),
        validation_reason=result.get("validation_reason"),
    )


@app.post("/copilot", response_model=CopilotResponse)
def copilot(request: CopilotRequest):
    operation = {
        "operation_id": "COPILOT-REQUEST",
        "type": "supply_chain_management",
        "priority": "normal",
        "location": "Warehouse A",
        "source": "copilot_studio",
    }

    result = chat(
        ChatRequest(
            message=request.question,
            operation=operation,
        )
    )

    return CopilotResponse(
        answer=result.response,
        trace_id=result.trace_id,
        validation_passed=result.validation_passed,
        validation_reason=result.validation_reason,
    )


if __name__ == "__main__":
    trace_id = new_trace_id()

    example = {
        "operation_id": "OP-12345",
        "type": "supply_chain_management",
        "priority": "high",
        "location": "Warehouse A",
    }

    result = graph.invoke(
        {
            "operation": example,
            "messages": [
                HumanMessage(
                    content="Evaluate supplier risk for Contoso Logistics."
                )
            ],
            "trace_id": trace_id,
            "validation_passed": None,
            "validation_reason": None,
            "validation_attempts": 0,
            "selected_route": None,
        }
    )

    print("\n=== FINAL MESSAGES ===")
    for m in result["messages"]:
        print(f"{m.type}: {m.content}")

    print(f"\nTrace ID: {trace_id}")
    print("Check logs/agent_events.jsonl for structured observability events.")
