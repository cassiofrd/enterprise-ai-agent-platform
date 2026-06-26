# Copilot Studio integration notes

The recommended integration endpoint is:

```text
POST /copilot
```

## Request schema

```json
{
  "question": "string"
}
```

## Response schema

```json
{
  "answer": "string",
  "trace_id": "string",
  "validation_passed": true,
  "validation_reason": "string"
}
```

## Why `/copilot` exists

`/chat` is a developer-oriented endpoint that accepts technical fields such as `operation`.

`/copilot` is an adapter endpoint for Copilot Studio and other chatbot frontends. It only requires the user's question and hides internal orchestration details.

## Flow

```text
Copilot Studio
↓
POST /copilot
↓
Supervisor Agent
↓
LangGraph
↓
Inventory Agent
↓
RAG / MCP
↓
Answer
```
