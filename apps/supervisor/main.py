from __future__ import annotations

import json
import operator
import time
from typing import Annotated, Literal, Optional, Sequence, TypedDict

import requests
from fastapi import FastAPI
from pydantic import BaseModel

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from shared.config import INVENTORY_AGENT_URL, SUPPLIER_AGENT_URL, ACTIVE_CHAT_MODEL
from shared.llm import get_chat_llm
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


class AgentState(TypedDict):
    operation: Optional[dict]
    messages: Annotated[Sequence[BaseMessage], operator.add]
    trace_id: str
    validation_passed: Optional[bool]
    validation_reason: Optional[str]
    validation_attempts: int


def supervisor_node(state: AgentState):
    history = state["messages"]
    operation = state.get("operation", {})
    operation_json = json.dumps(operation, ensure_ascii=False)
    trace_id = state.get("trace_id") or new_trace_id()

    supervisor_prompt = (
        "You are a supervisor coordinating a team of supply chain specialists.\n"
        "Currently available team members:\n"
        "- inventory: Handles inventory levels, reorder policy, stock, warehouse optimization, backorders, inventory policies, SKU-specific reorder policies, and inventory long-term memory.\n"
        "- supplier: Handles supplier risk, supplier comparison, supplier alternatives, lead time, SLA, vendor reliability, procurement recommendations, and supplier-specific memory.\n\n"
        "Routing rules:\n"
        "- Route questions about stock levels, reorder policy, backorders, inventory thresholds, warehouse policy, or SKU inventory operations to inventory.\n"
        "- Route questions about supplier risk, vendor evaluation, supplier comparison, alternate suppliers, supplier SLA, lead time, or procurement supplier recommendations to supplier.\n"
        "- If the user asks to remember a preferred supplier for a SKU, route to inventory for now, because preferred supplier memory is currently stored in the inventory memory layer.\n"
        "- If both inventory and supplier could help, choose the specialist that best matches the user's primary intent.\n\n"
        "Based on the user query, select ONE available team member to handle it.\n"
        "Output ONLY one of these names: inventory or supplier. Do not output anything else.\n\n"
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
    log_event("agent.supervisor.route", trace_id=trace_id, route=route)

    return {
        "messages": [AIMessage(content=route)],
        "trace_id": trace_id,
        "validation_attempts": state.get("validation_attempts", 0),
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


def route_to_specialist(state: AgentState) -> Literal["inventory", "supplier"]:
    agent_name = state["messages"][-1].content.strip().lower()
    trace_id = state.get("trace_id") or new_trace_id()

    if agent_name == "inventory":
        return "inventory"

    if agent_name == "supplier":
        return "supplier"

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
    g.add_node("validator", validator_node)
    g.add_node("improve_response", improve_response_node)

    g.set_entry_point("supervisor")

    g.add_conditional_edges(
        "supervisor",
        route_to_specialist,
        {"inventory": "inventory", "supplier": "supplier"},
    )
    g.add_edge("inventory", "validator")
    g.add_edge("supplier", "validator")
    g.add_conditional_edges(
        "validator",
        route_after_validation,
        {"end": END, "improve": "improve_response"},
    )
    g.add_edge("improve_response", END)

    return g.compile()


graph = construct_graph()


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
        "routes": ["inventory", "supplier"],
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
        }
    )

    print("\n=== FINAL MESSAGES ===")
    for m in result["messages"]:
        print(f"{m.type}: {m.content}")

    print(f"\nTrace ID: {trace_id}")
    print("Check logs/agent_events.jsonl for structured observability events.")
