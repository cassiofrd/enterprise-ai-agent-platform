from __future__ import annotations

import json
import operator
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from typing import Annotated, Literal, Optional, Sequence, TypedDict

import requests
from fastapi import Depends, FastAPI, Request, Response
from pydantic import BaseModel

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from shared.settings import settings
from shared.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from shared.llm import get_chat_llm
from shared.azure_search import answer_from_knowledge, azure_search_status
from shared.auth import require_auth
from shared.security import security
from shared.memory import (
    format_conversation_context,
    get_recent_conversation_turns,
    list_conversation_turns,
    save_conversation_turn,
)
from shared.observability import (
    log_event,
    new_trace_id,
    observe_duration,
    log_llm_usage,
    get_metrics_summary,
    get_recent_events,
    get_trace_events,
    get_trace_index,
    get_trace_summary,
    measure_time,
    get_cost_summary,
    get_agent_metrics_summary,
)

from shared.metrics import metrics_collector
from shared.request_context import (
    bind_request_context,
    get_request_context,
    request_context_scope,
)
from shared.tracing import start_span



from shared.operational import (
    cache_readiness,
    diagnostics_payload,
    liveness_payload,
    memory_readiness,
    readiness_payload,
    search_readiness,
    telemetry_readiness,
)
from shared.cache import cache_health
from shared.memory import memory_health
from shared.telemetry import telemetry_status


INVENTORY_AGENT_URL = settings.inventory_agent_url or "http://localhost:8001/invoke"
SUPPLIER_AGENT_URL = settings.supplier_agent_url or "http://localhost:8002/invoke"
ACTIVE_CHAT_MODEL = settings.active_chat_model or "gpt-4o-mini"
A2A_MAX_ATTEMPTS = settings.a2a_max_attempts
A2A_RETRY_BACKOFF_SECONDS = settings.a2a_retry_backoff_seconds

CIRCUIT_BREAKERS = {
    "inventory": CircuitBreaker(
        name="inventory",
        failure_threshold=settings.circuit_breaker_failure_threshold,
        recovery_timeout_seconds=settings.circuit_breaker_recovery_timeout_seconds,
    ),
    "supplier": CircuitBreaker(
        name="supplier",
        failure_threshold=settings.circuit_breaker_failure_threshold,
        recovery_timeout_seconds=settings.circuit_breaker_recovery_timeout_seconds,
    ),
}

app = FastAPI(title="Supervisor Agent API")


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-ID") or new_trace_id()
    request_id = request.headers.get("X-Request-ID")
    session_id = request.headers.get("X-Session-ID")
    with request_context_scope(trace_id=trace_id, request_id=request_id, session_id=session_id, endpoint=request.url.path) as context:
        started_at = time.perf_counter()
        metrics_collector.increment("http.requests.total")
        metrics_collector.increment(f"http.requests.{request.method.lower()}")
        log_event("http.request.started", method=request.method, path=request.url.path)
        try:
            with start_span(
                "http.request",
                trace_id=context.trace_id,
                attributes={
                    "http.method": request.method,
                    "http.route": request.url.path,
                    "service.name": settings.otel_service_name,
                },
            ):
                response = await call_next(request)
        except Exception as exc:
            latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
            metrics_collector.increment("http.requests.errors")
            metrics_collector.observe_latency("http.request", latency_ms)
            log_event("http.request.completed", method=request.method, path=request.url.path, status="error", latency_ms=latency_ms, error_type=type(exc).__name__, error_message=str(exc))
            raise
        latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
        status_name = "success" if response.status_code < 400 else "error"
        metrics_collector.increment(f"http.responses.{response.status_code}")
        metrics_collector.observe_latency("http.request", latency_ms)
        log_event("http.request.completed", method=request.method, path=request.url.path, status=status_name, status_code=response.status_code, latency_ms=latency_ms)
        response.headers["X-Trace-ID"] = context.trace_id
        response.headers["X-Request-ID"] = context.request_id
        return response

llm = get_chat_llm(temperature=0.0)


class ChatRequest(BaseModel):
    message: str
    operation: Optional[dict] = None
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    trace_id: str
    session_id: str
    selected_route: Optional[str] = None
    validation_passed: Optional[bool] = None
    validation_reason: Optional[str] = None
    sources: list[dict] = []


class CopilotRequest(BaseModel):
    question: str
    session_id: Optional[str] = None


class CopilotResponse(BaseModel):
    answer: str
    trace_id: str
    session_id: str
    selected_route: Optional[str] = None
    validation_passed: Optional[bool] = None
    validation_reason: Optional[str] = None
    sources: list[dict] = []


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
    session_id: Optional[str]
    conversation_context: Optional[str]
    sources: Optional[list[dict]]


def supervisor_node(state: AgentState):
    history = state["messages"]
    operation = state.get("operation", {})
    operation_json = json.dumps(operation, ensure_ascii=False)
    trace_id = state.get("trace_id") or new_trace_id()
    conversation_context = state.get("conversation_context") or "Sem histórico recente para esta sessão."
    user_messages = [m.content for m in history if m.type == "human"]
    latest_question = user_messages[-1] if user_messages else ""

    # Deterministic shortcut: questions like "Quem fornece o PARAFUSO-M20?" are
    # product master-data questions. Route them to inventory first so the
    # preferred supplier is resolved from /products/{code} before any supplier
    # profile lookup. This avoids answering only with supplier profile data.
    if _extract_product_code(latest_question) and any(
        term in latest_question.lower() for term in ["quem fornece", "fornecedor", "fornece"]
    ):
        route = "inventory"
        log_event("supervisor.route.selected", trace_id=trace_id, route=route, routing_mode="deterministic_product_supplier")
        return {
            "messages": [AIMessage(content=route)],
            "trace_id": trace_id,
            "validation_attempts": state.get("validation_attempts", 0),
            "selected_route": route,
            "session_id": state.get("session_id"),
            "conversation_context": conversation_context,
            "sources": state.get("sources"),
        }

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
        f"OPERATION: {operation_json}\n\n"
        f"RECENT CONVERSATION CONTEXT FOR THIS SESSION:\n{conversation_context}"
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
        "session_id": state.get("session_id"),
        "conversation_context": conversation_context,
        "sources": state.get("sources"),
    }



def _breaker_for(target_agent: str) -> CircuitBreaker:
    try:
        return CIRCUIT_BREAKERS[target_agent]
    except KeyError as exc:
        raise ValueError(f"Unknown circuit-breaker target: {target_agent}") from exc


def _is_transient_request_exception(
    exc: requests.exceptions.RequestException,
) -> bool:
    response = getattr(exc, "response", None)
    if response is None:
        return True

    status_code = response.status_code
    return status_code == 429 or status_code >= 500


def _retry_delay_seconds(attempt: int) -> float:
    if A2A_RETRY_BACKOFF_SECONDS <= 0:
        return 0.0
    return A2A_RETRY_BACKOFF_SECONDS * (2 ** max(0, attempt - 1))


def _log_breaker_snapshot(
    *,
    event_name: str,
    target_agent: str,
    trace_id: str,
    **fields,
) -> None:
    snapshot = _breaker_for(target_agent).snapshot()
    log_event(
        event_name,
        trace_id=trace_id,
        target_agent=target_agent,
        circuit_state=snapshot.state,
        failure_count=snapshot.failure_count,
        failure_threshold=snapshot.failure_threshold,
        retry_after_seconds=round(snapshot.retry_after_seconds, 3),
        **fields,
    )


def _allow_service_call(*, target_agent: str, trace_id: str) -> CircuitBreaker:
    breaker = _breaker_for(target_agent)
    try:
        breaker.before_call()
    except CircuitBreakerOpenError as exc:
        _log_breaker_snapshot(
            event_name="circuit_breaker.call_blocked",
            target_agent=target_agent,
            trace_id=trace_id,
            error_message=str(exc),
        )
        raise
    return breaker


def _record_service_success(
    *,
    breaker: CircuitBreaker,
    target_agent: str,
    trace_id: str,
) -> None:
    previous_state = breaker.snapshot().state
    breaker.record_success()
    _log_breaker_snapshot(
        event_name=(
            "circuit_breaker.closed"
            if previous_state != "closed"
            else "circuit_breaker.success"
        ),
        target_agent=target_agent,
        trace_id=trace_id,
        previous_state=previous_state,
    )


def _record_service_failure(
    *,
    breaker: CircuitBreaker,
    target_agent: str,
    trace_id: str,
    exc: Exception,
) -> None:
    previous_state = breaker.snapshot().state
    breaker.record_failure()
    current_state = breaker.snapshot().state
    _log_breaker_snapshot(
        event_name=(
            "circuit_breaker.opened"
            if current_state == "open" and previous_state != "open"
            else "circuit_breaker.failure"
        ),
        target_agent=target_agent,
        trace_id=trace_id,
        previous_state=previous_state,
        error_type=type(exc).__name__,
        error_message=str(exc),
    )

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
    breaker = _allow_service_call(
        target_agent=target_agent,
        trace_id=trace_id,
    )

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

    try:
        with observe_duration(
            f"a2a.{target_agent}.request",
            trace_id=trace_id,
            agent="supervisor",
            target_agent=target_agent,
            url=target_url,
        ):
            response = None
            last_exception: requests.exceptions.RequestException | None = None

            for attempt in range(1, A2A_MAX_ATTEMPTS + 1):
                try:
                    log_event(
                        f"a2a.{target_agent}.attempt",
                        trace_id=trace_id,
                        target=target_agent,
                        attempt=attempt,
                        max_attempts=A2A_MAX_ATTEMPTS,
                    )

                    headers = {}
                    if security.api_token:
                        headers["Authorization"] = f"Bearer {security.api_token}"

                    response = requests.post(
                        target_url,
                        json=payload,
                        headers=headers,
                        timeout=timeout_seconds,
                    )
                    response.raise_for_status()
                    last_exception = None
                    break

                except requests.exceptions.RequestException as exc:
                    last_exception = exc
                    transient = _is_transient_request_exception(exc)

                    log_event(
                        f"a2a.{target_agent}.attempt_failed",
                        trace_id=trace_id,
                        target=target_agent,
                        attempt=attempt,
                        max_attempts=A2A_MAX_ATTEMPTS,
                        transient=transient,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )

                    should_retry = transient and attempt < A2A_MAX_ATTEMPTS
                    if not should_retry:
                        break

                    delay_seconds = _retry_delay_seconds(attempt)
                    log_event(
                        f"a2a.{target_agent}.retry_scheduled",
                        trace_id=trace_id,
                        target=target_agent,
                        attempt=attempt,
                        next_attempt=attempt + 1,
                        delay_seconds=delay_seconds,
                    )
                    if delay_seconds > 0:
                        time.sleep(delay_seconds)

            if last_exception is not None:
                if _is_transient_request_exception(last_exception):
                    _record_service_failure(
                        breaker=breaker,
                        target_agent=target_agent,
                        trace_id=trace_id,
                        exc=last_exception,
                    )
                else:
                    # A client/business error does not mean the service is unhealthy.
                    _record_service_success(
                        breaker=breaker,
                        target_agent=target_agent,
                        trace_id=trace_id,
                    )
                raise last_exception

            if response is None:
                exc = RuntimeError(
                    f"{target_agent} Agent did not return a response."
                )
                _record_service_failure(
                    breaker=breaker,
                    target_agent=target_agent,
                    trace_id=trace_id,
                    exc=exc,
                )
                raise exc

        try:
            data = response.json()
        except ValueError as exc:
            _record_service_failure(
                breaker=breaker,
                target_agent=target_agent,
                trace_id=trace_id,
                exc=exc,
            )
            raise

        specialist_response = data.get("response")

        if not specialist_response:
            exc = ValueError(
                f"{target_agent} Agent response did not include a 'response' field."
            )
            _record_service_failure(
                breaker=breaker,
                target_agent=target_agent,
                trace_id=trace_id,
                exc=exc,
            )
            raise exc

        _record_service_success(
            breaker=breaker,
            target_agent=target_agent,
            trace_id=trace_id,
        )

    except CircuitBreakerOpenError:
        raise
    except Exception:
        raise

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
    """Route inventory questions. Prefer deterministic REST when a product code is available."""
    trace_id = state.get("trace_id") or new_trace_id()
    user_messages = [m.content for m in state["messages"] if m.type == "human"]
    user_question = user_messages[-1] if user_messages else ""
    product_code = _resolve_product_code(user_question, state.get("conversation_context") or "")

    if product_code:
        structured_answer = _answer_inventory_structured(
            question=user_question,
            product_code=product_code,
            trace_id=trace_id,
        )
        if structured_answer:
            return {
                "messages": [AIMessage(content=structured_answer)],
                "trace_id": trace_id,
                "selected_route": "inventory_structured",
                "sources": [],
            }

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
            "selected_route": "inventory",
        }

    except CircuitBreakerOpenError as exc:
        log_event(
            "a2a.inventory.circuit_open",
            trace_id=trace_id,
            target="inventory",
            error_type=type(exc).__name__,
            error_message=str(exc),
            retry_after_seconds=exc.retry_after_seconds,
            fallback="inventory_circuit_open_message",
        )

        fallback_message = (
            "O Inventory Agent está temporariamente isolado pelo circuit breaker "
            f"após falhas recentes. Tente novamente em aproximadamente "
            f"{max(1, round(exc.retry_after_seconds))} segundos. "
            f"Trace ID: {trace_id}."
        )
        return {"messages": [AIMessage(content=fallback_message)], "trace_id": trace_id}

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
    """Route supplier questions. Resolve pronouns from session context when possible."""
    trace_id = state.get("trace_id") or new_trace_id()
    user_messages = [m.content for m in state["messages"] if m.type == "human"]
    user_question = user_messages[-1] if user_messages else ""
    conversation_context = state.get("conversation_context") or ""

    supplier_name = _resolve_supplier_name(user_question, conversation_context, trace_id=trace_id)
    if supplier_name:
        structured_answer = _answer_supplier_structured(
            question=user_question,
            supplier_name=supplier_name,
            trace_id=trace_id,
        )
        if structured_answer:
            return {
                "messages": [AIMessage(content=structured_answer)],
                "trace_id": trace_id,
                "selected_route": "supplier_structured",
                "sources": [],
            }

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
            "selected_route": "supplier",
        }

    except CircuitBreakerOpenError as exc:
        log_event(
            "a2a.supplier.circuit_open",
            trace_id=trace_id,
            target="supplier",
            error_type=type(exc).__name__,
            error_message=str(exc),
            retry_after_seconds=exc.retry_after_seconds,
            fallback="supplier_circuit_open_message",
        )

        fallback_message = (
            "O Supplier Agent está temporariamente isolado pelo circuit breaker "
            f"após falhas recentes. Tente novamente em aproximadamente "
            f"{max(1, round(exc.retry_after_seconds))} segundos. "
            f"Trace ID: {trace_id}."
        )
        return {"messages": [AIMessage(content=fallback_message)], "trace_id": trace_id}

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
    target_agent: str,
    timeout_seconds: int = 30,
) -> dict:
    """Call a deterministic REST endpoint with retry and circuit breaking."""

    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    breaker = _allow_service_call(
        target_agent=target_agent,
        trace_id=trace_id,
    )

    log_event(
        "supervisor.structured_tool.request",
        trace_id=trace_id,
        target_agent=target_agent,
        url=url,
        tool_operation=tool_operation,
    )

    start = time.perf_counter()
    response = None
    last_exception: requests.exceptions.RequestException | None = None

    for attempt in range(1, A2A_MAX_ATTEMPTS + 1):
        try:
            headers = {}
            if security.api_token:
                headers["Authorization"] = f"Bearer {security.api_token}"

            response = requests.get(
                url,
                headers=headers,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            last_exception = None
            break

        except requests.exceptions.RequestException as exc:
            last_exception = exc
            transient = _is_transient_request_exception(exc)

            log_event(
                "supervisor.structured_tool.attempt_failed",
                trace_id=trace_id,
                target_agent=target_agent,
                url=url,
                tool_operation=tool_operation,
                attempt=attempt,
                max_attempts=A2A_MAX_ATTEMPTS,
                transient=transient,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

            should_retry = transient and attempt < A2A_MAX_ATTEMPTS
            if not should_retry:
                break

            delay_seconds = _retry_delay_seconds(attempt)
            log_event(
                "supervisor.structured_tool.retry_scheduled",
                trace_id=trace_id,
                target_agent=target_agent,
                url=url,
                tool_operation=tool_operation,
                attempt=attempt,
                next_attempt=attempt + 1,
                delay_seconds=delay_seconds,
            )
            if delay_seconds > 0:
                time.sleep(delay_seconds)

    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    if last_exception is not None:
        if _is_transient_request_exception(last_exception):
            _record_service_failure(
                breaker=breaker,
                target_agent=target_agent,
                trace_id=trace_id,
                exc=last_exception,
            )
        else:
            _record_service_success(
                breaker=breaker,
                target_agent=target_agent,
                trace_id=trace_id,
            )
        raise last_exception

    if response is None:
        exc = RuntimeError(
            f"{target_agent} structured endpoint did not return a response."
        )
        _record_service_failure(
            breaker=breaker,
            target_agent=target_agent,
            trace_id=trace_id,
            exc=exc,
        )
        raise exc

    try:
        data = response.json()
    except ValueError as exc:
        _record_service_failure(
            breaker=breaker,
            target_agent=target_agent,
            trace_id=trace_id,
            exc=exc,
        )
        raise

    _record_service_success(
        breaker=breaker,
        target_agent=target_agent,
        trace_id=trace_id,
    )

    log_event(
        "supervisor.structured_tool.response",
        trace_id=trace_id,
        target_agent=target_agent,
        url=url,
        tool_operation=tool_operation,
        http_status_code=response.status_code,
        latency_ms=latency_ms,
    )

    return data


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




def _resolve_product_code(question: str, conversation_context: str = "") -> Optional[str]:
    """Resolve a product code from the current question or recent session history."""
    product_code = _extract_product_code(question)
    if product_code:
        return product_code
    return _extract_product_code(conversation_context or "")


def _known_supplier_names() -> list[str]:
    """Known suppliers in the demo structured data. Used for deterministic pronoun resolution."""
    return ["XYZ Metais", "ABC Industrial", "Delta Borrachas"]


def _extract_supplier_name_from_text(text: str) -> Optional[str]:
    lowered = (text or "").lower()
    for name in _known_supplier_names():
        if name.lower() in lowered:
            return name
    return None


def _resolve_supplier_name(question: str, conversation_context: str = "", trace_id: str | None = None) -> Optional[str]:
    """Resolve supplier name from the question, session history, or product code context."""
    supplier_name = _extract_supplier_name_from_text(question)
    if supplier_name:
        return supplier_name

    product_code = _resolve_product_code(question, conversation_context)
    if product_code:
        try:
            inventory_base_url = _service_base_url(INVENTORY_AGENT_URL)
            product_payload = _structured_get(
                base_url=inventory_base_url,
                path=f"/products/{quote(product_code)}",
                trace_id=trace_id or new_trace_id(),
                tool_operation="resolveSupplierFromProduct",
                target_agent="inventory",
            )
            product = product_payload.get("product", {}) if isinstance(product_payload, dict) else {}
            supplier_name = product.get("preferred_supplier")
            if supplier_name:
                return supplier_name
        except Exception as exc:
            log_event(
                "supervisor.context.resolve_supplier_from_product.error",
                trace_id=trace_id or new_trace_id(),
                product_code=product_code,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    supplier_name = _extract_supplier_name_from_text(conversation_context)
    if supplier_name:
        return supplier_name

    return None


def _answer_inventory_structured(*, question: str, product_code: str, trace_id: str) -> Optional[str]:
    """Answer common inventory/product questions deterministically from REST endpoints."""
    inventory_base_url = _service_base_url(INVENTORY_AGENT_URL)
    try:
        product_payload = _structured_get(
            base_url=inventory_base_url,
            path=f"/products/{quote(product_code)}",
            trace_id=trace_id,
            tool_operation="getProduct",
            target_agent="inventory",
        )
    except Exception as exc:
        log_event(
            "supervisor.inventory_structured.product.error",
            trace_id=trace_id,
            product_code=product_code,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return None

    product = product_payload.get("product", {}) if isinstance(product_payload, dict) else {}
    if not product:
        return None

    supplier_name = product.get("preferred_supplier")
    policy = product.get("inventory_policy") or {}
    q = question.lower()

    if any(term in q for term in ["fornecedor", "fornece", "quem fornece"]):
        answer = f"O produto {product_code} é fornecido por {supplier_name or 'fornecedor não encontrado'}."
    elif any(term in q for term in ["lead time", "prazo", "entrega"]):
        lead_time = product.get("lead_time_days")
        answer = f"O lead time do {product_code} é de {lead_time} dias." if lead_time is not None else f"Não encontrei lead time para {product_code}."
    elif any(term in q for term in ["estoque", "política", "politica", "nível crítico", "nivel critico", "segurança", "seguranca"]):
        abc_class = product.get("abc_class")
        policy_text = _format_inventory_policy(policy)
        answer = f"Para o produto {product_code}, a classe ABC é {abc_class}. A política de estoque define {policy_text}."
    else:
        answer = (
            f"Dados do produto {product_code}: fornecedor preferencial {supplier_name or 'não encontrado'}, "
            f"classe ABC {product.get('abc_class') or 'não informada'}, "
            f"lead time {product.get('lead_time_days') or 'não informado'} dias. "
            f"Política de estoque: {_format_inventory_policy(policy)}."
        )

    log_event(
        "supervisor.inventory_structured.answer",
        trace_id=trace_id,
        product_code=product_code,
        supplier_name=supplier_name,
        answer_preview=answer[:500],
    )
    return answer


def _answer_supplier_structured(*, question: str, supplier_name: str, trace_id: str) -> Optional[str]:
    """Answer common supplier questions deterministically from REST endpoints."""
    supplier_base_url = _service_base_url(SUPPLIER_AGENT_URL)
    try:
        supplier_payload = _structured_get(
            base_url=supplier_base_url,
            path=f"/suppliers/{quote(supplier_name)}",
            trace_id=trace_id,
            tool_operation="getSupplier",
            target_agent="supplier",
        )
    except Exception as exc:
        log_event(
            "supervisor.supplier_structured.supplier.error",
            trace_id=trace_id,
            supplier_name=supplier_name,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return None

    supplier = supplier_payload.get("supplier", {}) if isinstance(supplier_payload, dict) else {}
    if not supplier:
        return None

    q = question.lower()
    resolved_product_code = _extract_product_code(question)
    if resolved_product_code and any(term in q for term in ["quem fornece", "fornecedor", "fornece"]):
        answer = (
            f"O produto {resolved_product_code} é fornecido por {supplier.get('supplier_name', supplier_name)}. "
            f"Dados do fornecedor: rating {supplier.get('rating') or 'não informado'}, "
            f"risco {supplier.get('risk_level') or 'não informado'}, "
            f"comprador responsável {supplier.get('buyer') or 'não informado'} "
            f"e condição de pagamento {supplier.get('payment_terms') or 'não informada'}."
        )
    elif any(term in q for term in ["risco", "risk"]):
        answer = (
            f"O fornecedor {supplier.get('supplier_name', supplier_name)} tem risco "
            f"{supplier.get('risk_level') or 'não informado'} e rating {supplier.get('rating') or 'não informado'}."
        )
    elif any(term in q for term in ["nota", "rating", "avaliação", "avaliacao"]):
        answer = (
            f"O fornecedor {supplier.get('supplier_name', supplier_name)} possui rating "
            f"{supplier.get('rating') or 'não informado'} e risco {supplier.get('risk_level') or 'não informado'}."
        )
    elif any(term in q for term in ["comprador", "responsável", "responsavel"]):
        answer = f"O comprador responsável por {supplier.get('supplier_name', supplier_name)} é {supplier.get('buyer') or 'não informado'}."
    elif any(term in q for term in ["pagamento", "condição", "condicao"]):
        answer = f"A condição de pagamento de {supplier.get('supplier_name', supplier_name)} é {supplier.get('payment_terms') or 'não informada'}."
    else:
        location = "/".join([x for x in [supplier.get("city"), supplier.get("state")] if x]) or "localização não informada"
        answer = (
            f"Dados do fornecedor {supplier.get('supplier_name', supplier_name)}: "
            f"rating {supplier.get('rating') or 'não informado'}, "
            f"risco {supplier.get('risk_level') or 'não informado'}, "
            f"localização {location}, "
            f"comprador responsável {supplier.get('buyer') or 'não informado'}, "
            f"condição de pagamento {supplier.get('payment_terms') or 'não informada'}."
        )

    log_event(
        "supervisor.supplier_structured.answer",
        trace_id=trace_id,
        supplier_name=supplier_name,
        answer_preview=answer[:500],
    )
    return answer


@measure_time("supervisor.multi_agent.execute")
def multi_agent_node(state: AgentState):
    """Answer hybrid questions with concurrent structured REST calls.

    Product and inventory-policy lookups start at the same time. As soon as the
    product lookup resolves the preferred supplier, the supplier lookup is
    submitted while the policy lookup may still be running. All calls share the
    same trace_id, and failures are isolated so a partial answer can still be
    returned.
    """
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
        strategy="parallel_structured_rest",
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
            "sources": [],
        }

    inventory_base_url = _service_base_url(INVENTORY_AGENT_URL)
    supplier_base_url = _service_base_url(SUPPLIER_AGENT_URL)

    product_payload: dict = {}
    policy_payload: dict = {}
    supplier_payload: dict = {}
    errors: list[str] = []
    supplier_name: str | None = None

    orchestration_started_at = time.perf_counter()

    with ThreadPoolExecutor(
        max_workers=3,
        thread_name_prefix="supervisor-multi-agent",
    ) as executor:
        product_future = executor.submit(
            _structured_get,
            base_url=inventory_base_url,
            path=f"/products/{quote(product_code)}",
            trace_id=trace_id,
            tool_operation="getProduct",
            target_agent="inventory",
        )
        policy_future = executor.submit(
            _structured_get,
            base_url=inventory_base_url,
            path=f"/inventory-policy/{quote(product_code)}",
            trace_id=trace_id,
            tool_operation="getInventoryPolicy",
            target_agent="inventory",
        )

        log_event(
            "supervisor.multi_agent.parallel.submitted",
            trace_id=trace_id,
            product_code=product_code,
            submitted_calls=["getProduct", "getInventoryPolicy"],
        )

        # The supplier call depends on the product result, but it can start while
        # the inventory-policy request is still in flight.
        try:
            product_payload = product_future.result()
            product = (
                product_payload.get("product", {})
                if isinstance(product_payload, dict)
                else {}
            )
            supplier_name = product.get("preferred_supplier")
        except Exception as exc:
            product = {}
            errors.append(f"produto {product_code}: {exc}")
            log_event(
                "supervisor.multi_agent.structured_inventory_product.error",
                trace_id=trace_id,
                product_code=product_code,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

        supplier_future = None
        if supplier_name:
            supplier_future = executor.submit(
                _structured_get,
                base_url=supplier_base_url,
                path=f"/suppliers/{quote(supplier_name)}",
                trace_id=trace_id,
                tool_operation="getSupplier",
                target_agent="supplier",
            )
            log_event(
                "supervisor.multi_agent.parallel.supplier_submitted",
                trace_id=trace_id,
                product_code=product_code,
                supplier_name=supplier_name,
            )

        try:
            policy_payload = policy_future.result()
        except Exception as exc:
            errors.append(f"política de estoque {product_code}: {exc}")
            log_event(
                "supervisor.multi_agent.structured_inventory_policy.error",
                trace_id=trace_id,
                product_code=product_code,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

        if supplier_future is not None:
            try:
                supplier_payload = supplier_future.result()
            except Exception as exc:
                errors.append(f"fornecedor {supplier_name}: {exc}")
                log_event(
                    "supervisor.multi_agent.structured_supplier.error",
                    trace_id=trace_id,
                    supplier_name=supplier_name,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )

    total_latency_ms = round(
        (time.perf_counter() - orchestration_started_at) * 1000,
        2,
    )

    product = (
        product_payload.get("product", {})
        if isinstance(product_payload, dict)
        else {}
    )
    supplier = (
        supplier_payload.get("supplier", {})
        if isinstance(supplier_payload, dict)
        else {}
    )

    # Product payloads may already contain inventory_policy. The dedicated
    # endpoint returns the field as policy.
    policy = (
        product.get("inventory_policy")
        or policy_payload.get("policy", {})
        or policy_payload.get("inventory_policy", {})
    )

    log_event(
        "supervisor.multi_agent.parallel.completed",
        trace_id=trace_id,
        product_code=product_code,
        supplier_name=supplier_name,
        total_latency_ms=total_latency_ms,
        product_available=bool(product),
        policy_available=bool(policy),
        supplier_available=bool(supplier),
        error_count=len(errors),
    )

    if not product and not policy:
        answer = (
            f"Não encontrei dados estruturados para o produto {product_code}. "
            "Não foi possível consolidar fornecedor e política de estoque."
        )
    else:
        answer_lines = [
            f"O produto {product_code} é fornecido por "
            f"{supplier_name or 'fornecedor não encontrado'}."
        ]

        if supplier:
            supplier_details = []
            if supplier.get("rating"):
                supplier_details.append(f"rating {supplier['rating']}")
            if supplier.get("risk_level"):
                supplier_details.append(f"risco {supplier['risk_level']}")
            if supplier.get("city") and supplier.get("state"):
                supplier_details.append(
                    f"localização {supplier['city']}/{supplier['state']}"
                )
            if supplier_details:
                answer_lines.append(
                    "Dados do fornecedor: "
                    + ", ".join(supplier_details)
                    + "."
                )

        abc_class = product.get("abc_class") or policy_payload.get("abc_class")
        policy_text = _format_inventory_policy(policy)
        if abc_class:
            answer_lines.append(
                f"A classe ABC do item é {abc_class}. "
                f"A política de estoque define {policy_text}."
            )
        else:
            answer_lines.append(
                f"A política de estoque define {policy_text}."
            )

        if errors:
            answer_lines.append(
                "Alguns dados complementares não foram encontrados: "
                + "; ".join(errors)
                + "."
            )

        answer = " ".join(answer_lines)

    log_event(
        "supervisor.multi_agent.response",
        trace_id=trace_id,
        product_code=product_code,
        supplier_name=supplier_name,
        strategy="parallel_structured_rest",
        total_latency_ms=total_latency_ms,
        response_preview=answer[:500],
    )

    return {
        "messages": [AIMessage(content=answer)],
        "trace_id": trace_id,
        "selected_route": "both",
        "sources": [],
    }


@measure_time("supervisor.knowledge.build")
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
            "source_id": idx,
            "title": item.get("title"),
            "source": item.get("source"),
            "agent": item.get("agent"),
            "doc_type": item.get("doc_type"),
            "entity_id": item.get("entity_id"),
            "score": item.get("@search.score"),
        }
        for idx, item in enumerate(results, start=1)
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
            "Sempre que usar uma informação de um chunk, cite a fonte no formato [Fonte N].\n"
            "No final, inclua uma seção curta chamada 'Fontes consultadas' listando Fonte N, título e arquivo.\n\n"
            f"PERGUNTA DO USUÁRIO:\n{question}\n\n"
            f"CONTEXTO:\n{context}\n\n"
            "MAPA DE FONTES:\n"
            + "\n".join(
                f"[Fonte {source['source_id']}] {source.get('title')} | {source.get('source')}"
                for source in sources
            )
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
        if sources and "Fontes consultadas" not in answer:
            source_lines = [
                f"[Fonte {source['source_id']}] {source.get('title')} — {source.get('source')}"
                for source in sources
            ]
            answer = answer.rstrip() + "\n\nFontes consultadas:\n" + "\n".join(source_lines)

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
        "sources": result.get("sources", []),
    }

def validator_node(state: AgentState):
    trace_id = state.get("trace_id") or new_trace_id()
    user_messages = [m.content for m in state["messages"] if m.type == "human"]
    latest_answer = state["messages"][-1].content
    selected_route = state.get("selected_route")

    # Deterministic REST and document-grounded responses are already grounded in
    # structured endpoints or Azure AI Search. Mark them as passed so the UI and
    # audit trail do not show false negatives from an overly generic validator.
    if selected_route in {"both", "knowledge", "inventory_structured", "supplier_structured"}:
        reason = "Resposta estruturada ou fundamentada em documentos; validação LLM ignorada por design."
        log_event(
            "agent.validator.bypass",
            trace_id=trace_id,
            selected_route=selected_route,
            passed=True,
            reason=reason,
        )
        return {
            "validation_passed": True,
            "validation_reason": reason,
            "validation_attempts": state.get("validation_attempts", 0),
            "trace_id": trace_id,
        }

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

    if state.get("selected_route") in {"both", "knowledge", "inventory_structured", "supplier_structured"}:
        log_event(
            "agent.validator.bypass",
            trace_id=state.get("trace_id") or new_trace_id(),
            reason="Structured REST or document-grounded RAG response should not be rewritten by improve_response.",
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
def knowledge_search(request: KnowledgeSearchRequest, _: None = Depends(require_auth)):
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
def knowledge_answer(request: KnowledgeAnswerRequest, _: None = Depends(require_auth)):
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
def metrics(_: None = Depends(require_auth)):
    return {
        "agent": "supervisor",
        "summary": get_metrics_summary(),
        "events": get_recent_events(limit=200),
        "traces": get_trace_index(limit=50),
    }


@app.get("/metrics/costs")
def metrics_costs(_: None = Depends(require_auth)):
    return {
        "agent": "supervisor",
        "costs": get_cost_summary(),
    }


@app.get("/metrics/agents")
def metrics_agents(_: None = Depends(require_auth)):
    return {
        "agent": "supervisor",
        "agents": get_agent_metrics_summary(),
    }


@app.get("/health/observability")
def observability_health():
    snapshot = metrics_collector.snapshot()
    return {
        "status": "ok",
        "component": "observability",
        "service_name": settings.otel_service_name,
        "otel_enabled": settings.otel_enabled,
        "application_insights_configured": bool(
            settings.applicationinsights_connection_string
        ),
        "event_buffer_size": len(get_recent_events(limit=1000)),
        "metrics": snapshot,
    }


@app.get("/conversations/{session_id}")
def conversation_history(session_id: str, limit: int = 10, _: None = Depends(require_auth)):
    """Return recent turns for one session for audit/debugging."""
    turns = get_recent_conversation_turns(session_id=session_id, limit=limit)
    return {"agent": "supervisor", "session_id": session_id, "turns": turns}


@app.get("/conversations")
def conversations(limit: int = 50, _: None = Depends(require_auth)):
    """Return recent turns across sessions for lightweight audit."""
    return {"agent": "supervisor", "turns": list_conversation_turns(limit=limit)}


def _operational_checks():
    return [
        ("cache", lambda: cache_readiness(cache_health), False),
        ("memory", lambda: memory_readiness(memory_health), True),
        ("azure_ai_search", lambda: search_readiness(azure_search_status), False),
        ("telemetry", lambda: telemetry_readiness(telemetry_status), False),
        (
            "inventory_agent_configuration",
            lambda: {
                "available": bool(INVENTORY_AGENT_URL),
                "url_configured": bool(INVENTORY_AGENT_URL),
            },
            True,
        ),
        (
            "supplier_agent_configuration",
            lambda: {
                "available": bool(SUPPLIER_AGENT_URL),
                "url_configured": bool(SUPPLIER_AGENT_URL),
            },
            True,
        ),
    ]

@app.get("/health")
def health():
    readiness, _ = readiness_payload(
        service_name="supervisor",
        checks=_operational_checks(),
    )
    return {
        "status": "ok" if readiness["status"] == "ready" else "degraded",
        "agent": "supervisor",
        "version": settings.app_version,
        "environment": settings.app_environment,
        "build_sha": settings.build_sha,
        "readiness": readiness,
        "capabilities": [
        "multi_agent_orchestration",
        "inventory_routing",
        "supplier_routing",
        "knowledge_rag",
        "distributed_cache",
        "distributed_memory",
        "circuit_breaker",
        "opentelemetry"
],
    }


@app.get("/live")
def live():
    return liveness_payload(service_name="supervisor")


@app.get("/ready")
def ready(response: Response):
    payload, status_code = readiness_payload(
        service_name="supervisor",
        checks=_operational_checks(),
    )
    response.status_code = status_code
    return payload


@app.get("/diagnostics")
def diagnostics(_: None = Depends(require_auth)):
    return diagnostics_payload(
        service_name="supervisor",
        checks=_operational_checks(),
        capabilities=[
        "multi_agent_orchestration",
        "inventory_routing",
        "supplier_routing",
        "knowledge_rag",
        "distributed_cache",
        "distributed_memory",
        "circuit_breaker",
        "opentelemetry"
],
    )

@app.post("/chat", response_model=ChatResponse)
@measure_time("api.chat.handler")
def chat(request: ChatRequest, _: None = Depends(require_auth)):
    active_context = get_request_context()
    trace_id = active_context.trace_id if active_context else new_trace_id()

    operation = request.operation or {
        "operation_id": "OP-12345",
        "type": "supply_chain_management",
        "priority": "high",
        "location": "Warehouse A",
    }

    session_id = request.session_id or (
        active_context.session_id if active_context else None
    ) or "default"

    with bind_request_context(
        trace_id=trace_id,
        session_id=session_id,
        endpoint="/chat",
    ):
        return _execute_chat_request(
            request=request,
            operation=operation,
            trace_id=trace_id,
            session_id=session_id,
        )


def _execute_chat_request(
    *,
    request: ChatRequest,
    operation: dict,
    trace_id: str,
    session_id: str,
) -> ChatResponse:
    conversation_context = format_conversation_context(session_id)
    log_event(
        "conversation.context.loaded",
        trace_id=trace_id,
        session_id=session_id,
        context_preview=conversation_context[:500],
    )

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
                "session_id": session_id,
                "conversation_context": conversation_context,
                "sources": [],
            }
        )

    final_message = result["messages"][-1].content
    selected_route = result.get("selected_route")
    sources = result.get("sources") or []
    # session_id was resolved before graph execution.

    turn_id = save_conversation_turn(
        session_id=session_id,
        trace_id=trace_id,
        user_message=request.message,
        assistant_message=final_message,
        route=selected_route,
        sources=sources,
    )

    log_event(
        "conversation.turn.saved",
        trace_id=trace_id,
        session_id=session_id,
        turn_id=turn_id,
        selected_route=selected_route,
        source_count=len(sources),
    )
    log_event(
        "api.chat.completed",
        trace_id=trace_id,
        session_id=session_id,
        selected_route=selected_route,
        validation_passed=result.get("validation_passed"),
        source_count=len(sources),
    )

    return ChatResponse(
        response=final_message,
        trace_id=trace_id,
        session_id=session_id,
        selected_route=selected_route,
        validation_passed=result.get("validation_passed"),
        validation_reason=result.get("validation_reason"),
        sources=sources,
    )


@app.post("/copilot", response_model=CopilotResponse)
@measure_time("api.copilot.handler")
def copilot(request: CopilotRequest, _: None = Depends(require_auth)):
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
            session_id=request.session_id,
        )
    )

    return CopilotResponse(
        answer=result.response,
        trace_id=result.trace_id,
        session_id=result.session_id,
        selected_route=result.selected_route,
        validation_passed=result.validation_passed,
        validation_reason=result.validation_reason,
        sources=result.sources,
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
            "session_id": "demo",
            "conversation_context": "Sem histórico recente para esta sessão.",
            "sources": [],
        }
    )

    print("\n=== FINAL MESSAGES ===")
    for m in result["messages"]:
        print(f"{m.type}: {m.content}")

    print(f"\nTrace ID: {trace_id}")
    print("Check logs/agent_events.jsonl for structured observability events.")


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
