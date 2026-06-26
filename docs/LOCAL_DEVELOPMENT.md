# Local Development Guide

## Why Uvicorn?

The Python files define FastAPI applications, but a server is required to expose them over HTTP. Uvicorn is the ASGI server that loads the `app` object and listens on a port.

Example:

```cmd
uvicorn apps.inventory_agent.main:app --reload --port 8001
```

This means:

- import `apps.inventory_agent.main`
- find the variable `app`
- serve it over HTTP on port `8001`

## Local vs Azure Container Apps

Local:

```text
Windows + Python + packages + .env
→ uvicorn apps.inventory_agent.main:app
→ localhost:8001
```

Azure Container Apps:

```text
Docker image with Python + packages + code
→ container starts uvicorn
→ public HTTPS endpoint
```

The application logic is the same in both environments.

## Recommended local test order

1. Start Inventory Agent.
2. Open `http://localhost:8001/docs`.
3. Test `/health`.
4. Test `/invoke` with a RAG question.
5. Save a memory.
6. Retrieve the memory.
7. Restart Uvicorn and retrieve the memory again.
8. Start Supervisor Agent.
9. Test routing through Supervisor.
