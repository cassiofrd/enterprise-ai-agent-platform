from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MemoryStore(ABC):
    """Backend-agnostic contract for long-term and conversation memory."""

    @abstractmethod
    def save_memory(
        self,
        *,
        key: str,
        value: str,
        memory_type: str,
        source_agent: str,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def search_memories(self, *, query: str, limit: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_memories(self, *, limit: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def delete_memory(self, *, memory_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def save_conversation_turn(
        self,
        *,
        session_id: str,
        user_message: str,
        assistant_message: str,
        trace_id: str | None,
        route: str | None,
        sources: list[dict[str, Any]],
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_recent_conversation_turns(
        self,
        *,
        session_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_conversation_turns(self, *, limit: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def health(self) -> dict[str, Any]:
        raise NotImplementedError
