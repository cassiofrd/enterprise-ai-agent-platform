# Architecture

## Purpose

This project implements a production-style multi-agent supply-chain copilot. The platform separates the enterprise agent layer, orchestration layer, specialist services, retrieval layer and observability layer.

The design goal is low coupling:

- Azure AI Foundry can be the enterprise entry point.
- The Supervisor API owns business orchestration.
- Specialist agents expose stable REST/OpenAPI tools.
- Azure AI Search stores structured entities and document knowledge.
- Observability records the full execution flow with `trace_id`.

## High-level component diagram

```mermaid
flowchart TD
    U[User]
    Teams[Teams / Microsoft 365 Copilot / Web App]
    ST[Streamlit Demo UI]
    AF[Azure AI Foundry Agent]
    SUP[Supervisor Agent API]
    INV[Inventory Agent API]
    SUPP[Supplier Agent API]
    AS[Azure AI Search]
    LLM[OpenAI / Azure OpenAI]
    MEM[Contextual Memory]
    OBS[Observability JSONL + Trace Explorer]
    ACA[Azure Container Apps]
    ACR[Azure Container Registry]
    GHA[GitHub Actions]

    U --> Teams
    U --> ST
    Teams --> AF
    ST --> AF
    ST --> SUP
    AF -->|OpenAPI tool: /copilot| SUP
    SUP --> INV
    SUP --> SUPP
    SUP --> AS
    INV --> AS
    SUPP --> AS
    SUP --> LLM
    INV --> LLM
    SUPP --> LLM
    SUP --> MEM
    INV --> MEM
    SUPP --> MEM
    SUP --> OBS
    INV --> OBS
    SUPP --> OBS
    GHA --> ACR
    ACR --> ACA
    ACA --> SUP
    ACA --> INV
    ACA --> SUPP
```

## Runtime modes

### Local development

```text
Streamlit → Supervisor localhost:8000 → Inventory localhost:8001 / Supplier localhost:8002 → Azure AI Search / LLM
```

### Azure Container Apps

```text
Streamlit or Foundry → Supervisor Container App → Inventory/Supplier Container Apps → Azure AI Search / LLM
```

### Enterprise channel

```text
Teams / Copilot → Azure AI Foundry Agent → OpenAPI tool → Supervisor Container App → Specialist agents
```

## Request flow: structured question

Example: `Quem fornece o PARAFUSO-M20?`

```mermaid
sequenceDiagram
    participant User
    participant Foundry as Azure AI Foundry / Streamlit
    participant Supervisor
    participant Inventory
    participant Search as Azure AI Search
    participant Memory
    participant Obs as Observability

    User->>Foundry: Quem fornece o PARAFUSO-M20?
    Foundry->>Supervisor: POST /copilot
    Supervisor->>Obs: start trace_id
    Supervisor->>Inventory: GET /products/PARAFUSO-M20
    Inventory->>Search: lookup product entity
    Search-->>Inventory: product + preferred supplier
    Inventory-->>Supervisor: XYZ Metais
    Supervisor->>Memory: save product_code and supplier_name in session
    Supervisor->>Obs: route + response events
    Supervisor-->>Foundry: answer + trace_id
    Foundry-->>User: O produto PARAFUSO-M20 é fornecido por XYZ Metais.
```

## Request flow: contextual follow-up

Example after the previous question: `Qual é o risco dele?`

```mermaid
sequenceDiagram
    participant User
    participant Supervisor
    participant Supplier
    participant Memory
    participant Search as Azure AI Search

    User->>Supervisor: Qual é o risco dele?
    Supervisor->>Memory: resolve previous supplier in session
    Memory-->>Supervisor: XYZ Metais
    Supervisor->>Supplier: GET /suppliers/XYZ Metais
    Supplier->>Search: lookup supplier entity
    Search-->>Supplier: rating A, risk low
    Supplier-->>Supervisor: supplier risk data
    Supervisor-->>User: O fornecedor XYZ Metais tem risco low e rating A.
```

## Request flow: document RAG

Example: `O que a política diz sobre estoque crítico?`

```mermaid
sequenceDiagram
    participant User
    participant Supervisor
    participant Search as Azure AI Search
    participant LLM
    participant Obs as Observability

    User->>Supervisor: Policy question
    Supervisor->>Obs: route selected = knowledge
    Supervisor->>Search: search relevant document chunks
    Search-->>Supervisor: policy, logistics and contract chunks
    Supervisor->>LLM: question + grounded context
    LLM-->>Supervisor: answer with citations
    Supervisor->>Obs: sources + latency + validation
    Supervisor-->>User: answer + sources + trace_id
```

## Deployment flow

```mermaid
flowchart LR
    Dev[Developer push] --> GH[GitHub Actions]
    GH --> Tests[Pytest]
    GH --> Build[ACR build]
    Build --> ACR[Azure Container Registry]
    ACR --> ACA[Azure Container Apps]
    ACA --> Inv[Inventory]
    ACA --> Sup[Supervisor]
    ACA --> Supplier[Supplier]
```

## Design decisions

### Foundry as enterprise layer

Azure AI Foundry is used as the enterprise-facing agent layer. It can publish or expose agents to enterprise channels while calling backend services through OpenAPI tools.

### Supervisor as business orchestrator

The Supervisor API keeps the business routing logic in code, where it can be tested, versioned and observed. This avoids hard-coding all business orchestration inside the Foundry portal.

### Specialist agents as APIs

Inventory and Supplier are independent FastAPI services. They can be called by the Supervisor, Azure AI Foundry, Copilot Studio or any other API client.

### Azure AI Search as knowledge layer

The same Azure AI Search service supports:

- structured lookups for products, suppliers and policies;
- document retrieval for RAG.

### Observability-first execution

Every meaningful request produces a `trace_id`. The `/traces` and `/traces/{trace_id}` endpoints reconstruct execution from JSONL events, enabling debugging and audit.
