from pathlib import Path

from shared import memory


def test_save_search_and_delete_memory(tmp_path, monkeypatch):
    db_path = tmp_path / "agent_memory.db"
    monkeypatch.setattr(memory, "DATA_DIR", tmp_path)
    monkeypatch.setattr(memory, "MEMORY_DB_PATH", db_path)

    memory_id = memory.save_memory(
        key="inventory:supplier:PARAFUSO-M20",
        value='{"type":"supplier","entity":"PARAFUSO-M20","value":"XYZ Metais"}',
        memory_type="supplier",
        source_agent="inventory",
    )

    results = memory.search_memories("PARAFUSO-M20")

    assert len(results) == 1
    assert results[0]["id"] == memory_id
    assert results[0]["key"] == "inventory:supplier:PARAFUSO-M20"
    assert "XYZ Metais" in results[0]["value"]

    assert memory.delete_memory(memory_id) is True
    assert memory.search_memories("PARAFUSO-M20") == []
