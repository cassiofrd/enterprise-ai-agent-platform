from __future__ import annotations

from dataclasses import dataclass

from dotenv import load_dotenv

from shared.security import security

load_dotenv()


@dataclass(frozen=True)
class AppSettings:
    # Runtime
    llm_provider: str | None
    active_chat_model: str | None
    active_embedding_model: str | None

    # OpenAI / Azure OpenAI
    openai_api_key: str | None

    # Azure AI Search
    azure_search_endpoint: str | None
    azure_search_api_key: str | None
    azure_search_admin_key: str | None
    azure_search_index: str | None
    azure_search_index_name: str | None

    # Security
    api_token: str | None

    # Azure AI Foundry
    azure_ai_project_endpoint: str | None
    foundry_agent_id: str | None
    foundry_agent_key: str | None
    foundry_agent_deployment_file: str | None

    # Agent URLs
    inventory_agent_url: str | None
    supplier_agent_url: str | None
    supervisor_url: str | None

    # Observability
    streamlit_observability_log: str | None


def load_settings() -> AppSettings:
    return AppSettings(
        llm_provider=security.get("LLM_PROVIDER"),
        active_chat_model=security.get("ACTIVE_CHAT_MODEL") or security.get("OPENAI_CHAT_MODEL"),
        active_embedding_model=security.get("ACTIVE_EMBEDDING_MODEL") or security.get("OPENAI_EMBEDDING_MODEL"),

        openai_api_key=security.get("OPENAI_API_KEY"),

        azure_search_endpoint=security.get("AZURE_SEARCH_ENDPOINT"),
        azure_search_api_key=security.get("AZURE_SEARCH_API_KEY"),
        azure_search_admin_key=security.get("AZURE_SEARCH_ADMIN_KEY"),
        azure_search_index=security.get("AZURE_SEARCH_INDEX"),
        azure_search_index_name=security.get("AZURE_SEARCH_INDEX_NAME"),

        api_token=security.get("API_TOKEN"),

        azure_ai_project_endpoint=security.get("AZURE_AI_PROJECT_ENDPOINT"),
        foundry_agent_id=security.get("FOUNDRY_AGENT_ID"),
        foundry_agent_key=security.get("FOUNDRY_AGENT_KEY"),
        foundry_agent_deployment_file=security.get("FOUNDRY_AGENT_DEPLOYMENT_FILE"),

        inventory_agent_url=security.get("INVENTORY_AGENT_URL"),
        supplier_agent_url=security.get("SUPPLIER_AGENT_URL"),
        supervisor_url=security.get("SUPERVISOR_URL"),

        streamlit_observability_log=security.get("STREAMLIT_OBSERVABILITY_LOG"),
    )


settings = load_settings()