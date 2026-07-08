from shared import memory


def test_conversation_turn_save_and_context(tmp_path, monkeypatch):
    db_path = tmp_path / "agent_memory.db"
    monkeypatch.setattr(memory, "DATA_DIR", tmp_path)
    monkeypatch.setattr(memory, "MEMORY_DB_PATH", db_path)

    turn_id = memory.save_conversation_turn(
        session_id="test-session",
        trace_id="trace-1",
        user_message="Quem fornece o PARAFUSO-M20?",
        assistant_message="O fornecedor é XYZ Metais.",
        route="both",
        sources=[{"title": "Contrato XYZ", "source": "contract.md"}],
    )

    turns = memory.get_recent_conversation_turns("test-session")
    assert len(turns) == 1
    assert turns[0]["id"] == turn_id
    assert turns[0]["sources"][0]["title"] == "Contrato XYZ"

    context = memory.format_conversation_context("test-session")
    assert "PARAFUSO-M20" in context
    assert "XYZ Metais" in context
