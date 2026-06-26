from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel


class MessageDTO(BaseModel):
    type: str
    content: str


class InventoryRequest(BaseModel):
    operation: Optional[dict[str, Any]] = None
    messages: list[MessageDTO]
    trace_id: Optional[str] = None


class InventoryResponse(BaseModel):
    agent: str
    response: str
    messages: list[MessageDTO]
    trace_id: Optional[str] = None


class SupplierRequest(BaseModel):
    operation: Optional[dict[str, Any]] = None
    messages: list[MessageDTO]
    trace_id: Optional[str] = None


class SupplierResponse(BaseModel):
    agent: str
    response: str
    messages: list[MessageDTO]
    trace_id: Optional[str] = None
