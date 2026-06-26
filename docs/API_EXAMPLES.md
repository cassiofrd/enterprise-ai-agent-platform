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
