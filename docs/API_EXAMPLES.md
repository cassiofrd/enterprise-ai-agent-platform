# API Examples

## Inventory Agent

### Health

```cmd
curl http://localhost:8001/health
```

### Invoke

```cmd
curl -X POST "http://localhost:8001/invoke" ^
  -H "Content-Type: application/json" ^
  -d "{\"operation\":{},\"messages\":[{\"type\":\"human\",\"content\":\"O que é estoque de segurança?\"}],\"trace_id\":\"local-rag-001\"}"
```

### Save memory

```cmd
curl -X POST "http://localhost:8001/invoke" ^
  -H "Content-Type: application/json" ^
  -d "{\"operation\":{},\"messages\":[{\"type\":\"human\",\"content\":\"Registre que o fornecedor do PARAFUSO-M20 é XYZ Metais.\"}],\"trace_id\":\"local-memory-001\"}"
```

### Retrieve memory

```cmd
curl -X POST "http://localhost:8001/invoke" ^
  -H "Content-Type: application/json" ^
  -d "{\"operation\":{},\"messages\":[{\"type\":\"human\",\"content\":\"Qual o fornecedor do PARAFUSO-M20?\"}],\"trace_id\":\"local-memory-002\"}"
```

### Search memories

```cmd
curl "http://localhost:8001/memories/search?query=PARAFUSO-M20"
```

### Metrics

```cmd
curl http://localhost:8001/metrics
```

## Supervisor Agent

```cmd
curl -X POST "http://localhost:8000/chat" ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"Qual o fornecedor do PARAFUSO-M20?\",\"operation\":{}}"
```

## Copilot endpoint

```cmd
curl -X POST "http://localhost:8000/copilot" ^
  -H "Content-Type: application/json" ^
  -d "{\"question\":\"Qual o fornecedor do PARAFUSO-M20?\"}"
```

## OpenAPI tool endpoints for Azure AI Foundry

These endpoints expose specific business capabilities in a simple REST format.
They are easier to consume as OpenAPI tools than the conversational `/invoke`
endpoint.

### Product lookup

```http
GET /products/PARAFUSO-M20
```

Expected response excerpt:

```json
{
  "agent": "inventory",
  "product": {
    "code": "PARAFUSO-M20",
    "abc_class": "B",
    "preferred_supplier": "XYZ Metais",
    "lead_time_days": 14,
    "inventory_policy": {
      "safety_stock_units": 200,
      "replenishment_frequency": "biweekly",
      "review_frequency": "weekly",
      "critical_level_units": 100
    }
  }
}
```

### Inventory policy lookup

```http
GET /inventory-policy/PARAFUSO-M20
```

### Supplier product lookup

```http
GET /suppliers/XYZ%20Metais/products
```

### Purchasing policy lookup

```http
GET /purchasing-policy
```


## OpenAPI Tool Observability

The structured tool endpoints log deterministic observability events whenever they are called by Azure AI Foundry or any other HTTP client.

Tracked fields include:

- `event_type`: `api.openapi_tool.call`
- `endpoint`: called endpoint template, such as `/products/{code}`
- `method`: HTTP method
- `tool_operation`: OpenAPI operation name, such as `getProduct`
- `status`: `success` or `error`
- `http_status_code`: HTTP response status
- `latency_ms`: measured endpoint execution time
- input-specific fields such as `product_code` or `supplier_name`

These events are available through:

```text
GET /metrics
```

and are displayed in the Streamlit Observability page.
