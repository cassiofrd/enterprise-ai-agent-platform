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

try:
    from azure.identity import DefaultAzureCredential
    from azure.ai.agents import AgentsClient
except Exception:  # pragma: no cover - only used when optional deps are missing
    DefaultAzureCredential = None
    AgentsClient = None

load_dotenv()

DEFAULT_SUPERVISOR_URL = "https://supervisor-agent.politedune-38af7eb9.brazilsouth.azurecontainerapps.io/copilot"
DEFAULT_DEPLOYMENT_FILE = "deployment/foundry_agents.json"
DEFAULT_AGENT_KEY = "supervisor_agent"
DEFAULT_OBSERVABILITY_LOG = "logs/streamlit_foundry_events.jsonl"


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


st.set_page_config(
    page_title="Supply Chain AI Assistant",
    page_icon="🔗",
    layout="centered",
)

st.title("🔗 Supply Chain AI Assistant")
st.caption(
    "Interface Streamlit conectada ao Azure AI Foundry, com fallback para o Supervisor em Azure Container Apps."
)


def _load_foundry_agent_id(deployment_file: str, agent_key: str) -> str | None:
    path = Path(deployment_file)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    value = data.get(agent_key)
    return str(value) if value else None


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


def ask_supervisor(question: str, api_url: str) -> dict[str, Any]:
    response = requests.post(
        api_url,
        json={"question": question},
        timeout=180,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "answer": data.get("answer", "No answer returned."),
        "raw": data,
    }


with st.sidebar:
    st.header("Runtime")
    runtime = st.radio(
        "Backend",
        ["Azure AI Foundry", "Supervisor Container Apps"],
        index=0,
    )

    if runtime == "Azure AI Foundry":
        project_endpoint = st.text_input(
            "Foundry project endpoint",
            value=os.getenv("AZURE_AI_PROJECT_ENDPOINT", ""),
            help="Exemplo: https://...services.ai.azure.com/api/projects/...",
        )
        deployment_file = st.text_input(
            "Agent deployment file",
            value=os.getenv("FOUNDRY_AGENT_DEPLOYMENT_FILE", DEFAULT_DEPLOYMENT_FILE),
        )
        agent_key = st.text_input(
            "Agent key",
            value=os.getenv("FOUNDRY_AGENT_KEY", DEFAULT_AGENT_KEY),
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

        if detected_agent_id:
            st.success(f"Agent ID carregado de {deployment_file}")
        else:
            st.warning("Agent ID não encontrado no arquivo de deployment.")

    else:
        supervisor_url = st.text_input(
            "Copilot endpoint",
            value=os.getenv("SUPERVISOR_URL", DEFAULT_SUPERVISOR_URL),
        )

    st.divider()
    st.subheader("Observabilidade")
    last_event = st.session_state.get("last_execution")
    if last_event:
        st.metric("Tempo total", _format_seconds(last_event.get("duration_seconds")))
        st.write(f"**Backend:** {last_event.get('backend')}")
        st.write(f"**Status:** {last_event.get('status')}")
        if last_event.get("agent_id"):
            st.caption(f"Agent ID: {last_event.get('agent_id')}")
        if last_event.get("run_id"):
            st.caption(f"Run ID: {last_event.get('run_id')}")
        if last_event.get("error"):
            st.error(last_event.get("error"))
    else:
        st.caption("Nenhuma execução registrada nesta sessão.")

    st.caption(f"Log local: {os.getenv('STREAMLIT_OBSERVABILITY_LOG', DEFAULT_OBSERVABILITY_LOG)}")

    if st.button("Limpar conversa"):
        st.session_state.messages = []
        st.session_state.last_execution = None
        st.session_state.execution_history = []
        st.rerun()

if "messages" not in st.session_state:
    st.session_state.messages = []
if "execution_history" not in st.session_state:
    st.session_state.execution_history = []

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
        }

        try:
            if runtime == "Azure AI Foundry":
                if not project_endpoint:
                    raise ValueError("AZURE_AI_PROJECT_ENDPOINT não foi informado.")
                if not agent_id:
                    raise ValueError("Foundry Agent ID não foi informado.")

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
                with st.spinner("Chamando Supervisor em Azure Container Apps..."):
                    result = ask_supervisor(question, supervisor_url)

                event.update(
                    {
                        "status": "success",
                        "supervisor_url": supervisor_url,
                    }
                )

                st.markdown(result["answer"])
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
