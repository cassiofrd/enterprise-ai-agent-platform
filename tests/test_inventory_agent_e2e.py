import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("LLM_PROVIDER", "openai")
os.environ.setdefault("API_TOKEN", "CHANGE_ME")

from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

from apps.inventory_agent import main as inventory_main  # noqa: E402
from shared import memory  # noqa: E402


AUTH_HEADERS = {"Authorization": "Bearer CHANGE_ME"}


class DummyInventoryLLM:
    """Small deterministic LLM double used to test the API flow without external calls."""

    def invoke(self, messages):
        system_prompt = messages[0].content if messages else ""
        user_text = messages[-1].content if messages else ""

        if "XYZ Metais" in system_prompt and "PARAFUSO-M20" in user_text:
            return AIMessage(
                content=(
                    "O fornecedor do PARAFUSO-M20 é XYZ Metais. "
                    "Essa informação está registrada na memória de longo prazo."
                )
            )

        return AIMessage(content="Não encontrei informação relevante na memória de longo prazo.")


def test_inventory_agent_memory_save_and_retrieve_end_to_end(tmp_path, monkeypatch):
    db_path = tmp_path / "agent_memory.db"
    monkeypatch.setattr(memory, "DATA_DIR", tmp_path)
    monkeypatch.setattr(memory, "MEMORY_DB_PATH", db_path)
    monkeypatch.setattr(inventory_main, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(inventory_main, "MEMORY_DB_PATH", db_path, raising=False)
    monkeypatch.setattr(inventory_main, "inventory_llm", DummyInventoryLLM())

    client = TestClient(inventory_main.app)

    save_response = client.post(
        "/invoke",
        headers=AUTH_HEADERS,
        json={
            "operation": {},
            "messages": [
                {
                    "type": "human",
                    "content": "Registre que o fornecedor do PARAFUSO-M20 é XYZ Metais.",
                }
            ],
            "trace_id": "test-e2e-memory-save",
        },
    )

    assert save_response.status_code == 200
    assert "Long-term memory saved successfully" in save_response.json()["response"]

    retrieve_response = client.post(
        "/invoke",
        headers=AUTH_HEADERS,
        json={
            "operation": {},
            "messages": [
                {
                    "type": "human",
                    "content": "Qual o fornecedor do PARAFUSO-M20?",
                }
            ],
            "trace_id": "test-e2e-memory-retrieve",
        },
    )

    assert retrieve_response.status_code == 200
    assert "XYZ Metais" in retrieve_response.json()["response"]
    assert "memória de longo prazo" in retrieve_response.json()["response"]
