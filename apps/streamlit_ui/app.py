from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import streamlit as st
from dotenv import load_dotenv

from shared.security import security

try:
    from azure.identity import DefaultAzureCredential
    from azure.ai.agents import AgentsClient
except Exception:  # pragma: no cover - optional local dependency
    DefaultAzureCredential = None
    AgentsClient = None

load_dotenv()

DEFAULT_SUPERVISOR_URL = (
    "https://supervisor-agent.politedune-38af7eb9.brazilsouth.azurecontainerapps.io/copilot"
)
DEFAULT_DEPLOYMENT_FILE = "deployment/foundry_agents.json"
DEFAULT_AGENT_KEY = "supervisor_agent"
DEFAULT_OBSERVABILITY_LOG = "logs/streamlit_foundry_events.jsonl"

OPENAPI_BY_AGENT_KEY = {
    "supervisor_agent": "openapi/foundry_supervisor_tools.openapi.json",
    "inventory_agent": "openapi/foundry_inventory_tools.openapi.json",
    "supplier_agent": "openapi/foundry_supplier_tools.openapi.json",
}

FLOW_BY_AGENT_KEY = {
    "supervisor_agent": (
        "Streamlit → Azure AI Foundry → Supervisor OpenAPI Tool → "
        "Supervisor Container Apps → Inventory + Supplier"
    ),
    "inventory_agent": "Streamlit → Azure AI Foundry → Inventory OpenAPI Tool → Inventory Container Apps",
    "supplier_agent": "Streamlit → Azure AI Foundry → Supplier OpenAPI Tool → Supplier Container Apps",
}

ORCHESTRATION_BY_AGENT_KEY = {
    "supervisor_agent": "multi-agent orchestration",
    "inventory_agent": "single specialist agent",
    "supplier_agent": "single specialist agent",
}


st.set_page_config(
    page_title="Supply Chain AI Assistant",
    page_icon="🔗",
    layout="centered",
)

st.title("🔗 Supply Chain AI Assistant")
st.caption(
    "Interface Streamlit conectada ao Azure AI Foundry, com fallback para o Supervisor em Azure Container Apps."
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: str, event: dict[str, Any]) -> None:
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    return f"{seconds:.2f}s"


def _load_deployment_ids(deployment_file: str) -> dict[str, str]:
    path = Path(deployment_file)
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    return {str(key): str(value) for key, value in data.items() if value}


def _load_foundry_agent_id(deployment_file: str, agent_key: str) -> str | None:
    return _load_deployment_ids(deployment_file).get(agent_key)


def _load_openapi_server_url(agent_key: str) -> str | None:
    openapi_path = Path(OPENAPI_BY_AGENT_KEY.get(agent_key, ""))
    if not openapi_path.exists():
        return None

    try:
        spec = json.loads(openapi_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    servers = spec.get("servers") or []
    if not servers:
        return None

    url = servers[0].get("url")
    return str(url) if url else None


def _extract_assistant_text(messages: Any) -> str:
    """Extract the latest assistant text from azure-ai-agents message objects."""
    answers: list[str] = []

    for message in messages:
        if getattr(message, "role", None) != "assistant":
            continue

        for content in getattr(message, "content", []) or []:
            if getattr(content, "type", None) == "text":
                text_obj = getattr(content, "text", None)
                value = getattr(text_obj, "value", None)
                if value:
                    answers.append(str(value))

    if not answers:
        return "Nenhuma resposta foi retornada pelo agente do Foundry."

    return answers[-1]


def ask_foundry_agent(question: str, project_endpoint: str, agent_id: str) -> dict[str, Any]:
    if AgentsClient is None or DefaultAzureCredential is None:
        raise RuntimeError(
            "As dependências azure-ai-agents e azure-identity não estão instaladas. "
            "Execute: pip install azure-ai-agents azure-identity python-dotenv"
        )

    client = AgentsClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential(),
    )

    run = client.create_thread_and_process_run(
        agent_id=agent_id,
        thread={
            "messages": [
                {
                    "role": "user",
                    "content": question,
                }
            ]
        },
    )

    messages = client.messages.list(thread_id=run.thread_id)
    answer = _extract_assistant_text(messages)

    return {
        "answer": answer,
        "agent_id": agent_id,
        "run_id": getattr(run, "id", None),
        "thread_id": getattr(run, "thread_id", None),
        "status": str(getattr(run, "status", "unknown")),
    }


def auth_headers() -> dict[str, str]:
    if security.api_token:
        return {"Authorization": f"Bearer {security.api_token}"}
    return {}


def ask_supervisor(question: str, api_url: str, session_id: str | None = None) -> dict[str, Any]:
    payload = {"question": question}
    if session_id:
        payload["session_id"] = session_id

    response = requests.post(
        api_url,
        json=payload,
        headers=auth_headers(),
        timeout=180,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "answer": data.get("answer", "No answer returned."),
        "trace_id": data.get("trace_id"),
        "validation_passed": data.get("validation_passed"),
        "validation_reason": data.get("validation_reason"),
        "session_id": data.get("session_id"),
        "selected_route": data.get("selected_route"),
        "sources": data.get("sources") or [],
        "raw": data,
    }


def _current_flow(runtime: str, agent_key: str | None = None) -> str:
    if runtime == "Azure AI Foundry":
        return FLOW_BY_AGENT_KEY.get(
            agent_key or DEFAULT_AGENT_KEY,
            "Streamlit → Azure AI Foundry → OpenAPI Tool → Container Apps",
        )
    return "Streamlit → Supervisor Container Apps → Inventory + Supplier"


if "messages" not in st.session_state:
    st.session_state.messages = []
if "execution_history" not in st.session_state:
    st.session_state.execution_history = []
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())


with st.sidebar:
    st.header("Runtime")
    runtime = st.radio(
        "Backend",
        ["Azure AI Foundry", "Supervisor Container Apps"],
        index=0,
    )

    project_endpoint = ""
    deployment_file = os.getenv("FOUNDRY_AGENT_DEPLOYMENT_FILE", DEFAULT_DEPLOYMENT_FILE)
    agent_key = DEFAULT_AGENT_KEY
    agent_id = ""
    detected_agent_id = None
    tool_endpoint = None
    supervisor_url = os.getenv("SUPERVISOR_URL", DEFAULT_SUPERVISOR_URL)

    if runtime == "Azure AI Foundry":
        project_endpoint = st.text_input(
            "Foundry project endpoint",
            value=os.getenv("AZURE_AI_PROJECT_ENDPOINT", ""),
            help="Exemplo: https://...services.ai.azure.com/api/projects/...",
        )
        deployment_file = st.text_input(
            "Agent deployment file",
            value=deployment_file,
        )

        known_agent_keys = ["supervisor_agent", "inventory_agent", "supplier_agent"]
        env_agent_key = os.getenv("FOUNDRY_AGENT_KEY", DEFAULT_AGENT_KEY)
        default_agent_index = known_agent_keys.index(env_agent_key) if env_agent_key in known_agent_keys else 0
        agent_key = st.selectbox(
            "Agent key",
            known_agent_keys,
            index=default_agent_index,
            help="Perfil do agente do Foundry que será chamado pela interface.",
        )

        detected_agent_id = _load_foundry_agent_id(deployment_file, agent_key)
        key_specific_agent_id = os.getenv(f"FOUNDRY_{agent_key.upper()}_ID", "")
        agent_id = st.text_input(
            "Foundry Agent ID",
            value=os.getenv("FOUNDRY_AGENT_ID", key_specific_agent_id or detected_agent_id or ""),
            help=(
                "Pode vir de FOUNDRY_AGENT_ID, de uma variável específica como "
                "FOUNDRY_SUPERVISOR_AGENT_ID, ou de deployment/foundry_agents.json."
            ),
        )

        tool_endpoint = _load_openapi_server_url(agent_key)

        if detected_agent_id:
            st.success(f"Agent ID carregado de {deployment_file}")
        else:
            st.warning("Agent ID não encontrado no arquivo de deployment.")

    else:
        supervisor_url = st.text_input(
            "Copilot endpoint",
            value=supervisor_url,
        )
        agent_key = "container_apps_supervisor"

    st.divider()
    st.subheader("Arquitetura ativa")
    active_flow = _current_flow(runtime, agent_key)
    st.code(active_flow)

    if runtime == "Azure AI Foundry":
        st.write(f"**Agent key:** `{agent_key}`")
        st.write(f"**Modo:** {ORCHESTRATION_BY_AGENT_KEY.get(agent_key, 'OpenAPI tool calling')}")
        if tool_endpoint:
            st.write(f"**Tool endpoint:** `{tool_endpoint}`")
        if agent_key == "supervisor_agent":
            st.info(
                "Neste modo, o Foundry conversa com a ferramenta OpenAPI do Supervisor. "
                "O Supervisor no Container Apps continua orquestrando Inventory e Supplier."
            )
    else:
        st.write("**Modo:** direct multi-agent orchestration")
        st.write(f"**Supervisor endpoint:** `{supervisor_url}`")

    st.divider()
    st.subheader("Observabilidade")
    last_event = st.session_state.get("last_execution")
    if last_event:
        st.metric("Tempo total", _format_seconds(last_event.get("duration_seconds")))
        st.write(f"**Backend:** {last_event.get('backend')}")
        st.write(f"**Status:** {last_event.get('status')}")
        if last_event.get("orchestration_mode"):
            st.write(f"**Modo:** {last_event.get('orchestration_mode')}")
        if last_event.get("trace_id"):
            st.caption(f"Trace ID: {last_event.get('trace_id')}")
        if last_event.get("agent_key"):
            st.caption(f"Agent key: {last_event.get('agent_key')}")
        if last_event.get("agent_id"):
            st.caption(f"Agent ID: {last_event.get('agent_id')}")
        if last_event.get("run_id"):
            st.caption(f"Run ID: {last_event.get('run_id')}")
        if last_event.get("error"):
            st.error(last_event.get("error"))
    else:
        st.caption("Nenhuma execução registrada nesta sessão.")

    st.caption(f"Log local: {os.getenv('STREAMLIT_OBSERVABILITY_LOG', DEFAULT_OBSERVABILITY_LOG)}")

    st.caption(f"Session ID: `{st.session_state.get('session_id', '-')}`")

    if st.button("Limpar conversa"):
        st.session_state.messages = []
        st.session_state.last_execution = None
        st.session_state.execution_history = []
        st.session_state.session_id = str(uuid.uuid4())
        st.rerun()

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

question = st.chat_input("Pergunte sobre estoque, fornecedores ou políticas de suprimentos...")

if question:
    st.session_state.messages.append({"role": "user", "content": question})

    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        execution_id = str(uuid.uuid4())
        started_at = _utc_now_iso()
        start = time.perf_counter()
        event: dict[str, Any] = {
            "execution_id": execution_id,
            "started_at": started_at,
            "backend": runtime,
            "question": question,
            "status": "started",
            "flow": _current_flow(runtime, agent_key),
            "agent_key": agent_key,
        }

        try:
            if runtime == "Azure AI Foundry":
                if not project_endpoint:
                    raise ValueError("AZURE_AI_PROJECT_ENDPOINT não foi informado.")
                if not agent_id:
                    raise ValueError("Foundry Agent ID não foi informado.")

                event.update(
                    {
                        "project_endpoint": project_endpoint,
                        "agent_id": agent_id,
                        "tool_endpoint": tool_endpoint,
                        "orchestration_mode": ORCHESTRATION_BY_AGENT_KEY.get(agent_key),
                    }
                )

                with st.spinner("Chamando Azure AI Foundry Agent..."):
                    result = ask_foundry_agent(question, project_endpoint, agent_id)

                event.update(
                    {
                        "status": "success",
                        "agent_id": result.get("agent_id"),
                        "run_id": result.get("run_id"),
                        "thread_id": result.get("thread_id"),
                        "run_status": result.get("status"),
                    }
                )

                st.markdown(result["answer"])
                st.session_state.messages.append(
                    {"role": "assistant", "content": result["answer"]}
                )
            else:
                event.update(
                    {
                        "supervisor_url": supervisor_url,
                        "orchestration_mode": "direct multi-agent orchestration",
                    }
                )

                with st.spinner("Chamando Supervisor em Azure Container Apps..."):
                    result = ask_supervisor(question, supervisor_url, st.session_state.session_id)

                event.update(
                    {
                        "status": "success",
                        "supervisor_url": supervisor_url,
                        "trace_id": result.get("trace_id"),
                        "validation_passed": result.get("validation_passed"),
                        "session_id": result.get("session_id"),
                        "selected_route": result.get("selected_route"),
                        "source_count": len(result.get("sources") or []),
                    }
                )

                st.markdown(result["answer"])
                if result.get("sources"):
                    with st.expander("Fontes recuperadas"):
                        st.json(result["sources"])
                st.session_state.messages.append(
                    {"role": "assistant", "content": result["answer"]}
                )

            event["duration_seconds"] = time.perf_counter() - start
            event["finished_at"] = _utc_now_iso()
            st.session_state.last_execution = event
            st.session_state.execution_history.append(event)
            _append_jsonl(os.getenv("STREAMLIT_OBSERVABILITY_LOG", DEFAULT_OBSERVABILITY_LOG), event)

            with st.expander("Detalhes da execução"):
                st.json({"result": result, "observability": event})

        except Exception as exc:
            event.update(
                {
                    "status": "error",
                    "error": str(exc),
                    "duration_seconds": time.perf_counter() - start,
                    "finished_at": _utc_now_iso(),
                }
            )
            st.session_state.last_execution = event
            st.session_state.execution_history.append(event)
            _append_jsonl(os.getenv("STREAMLIT_OBSERVABILITY_LOG", DEFAULT_OBSERVABILITY_LOG), event)

            st.error(f"Falha ao processar a pergunta: {exc}")
            st.info(
                "Se estiver usando Azure AI Foundry localmente, autentique-se com Azure CLI/VS Code "
                "ou execute a interface em um ambiente onde DefaultAzureCredential consiga obter token."
            )
            with st.expander("Detalhes da execução"):
                st.json(event)
