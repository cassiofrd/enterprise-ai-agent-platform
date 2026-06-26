# Memory Improvements - v6.1

This version improves the Inventory Agent long-term memory behavior.

## What changed

- Product/SKU extraction now supports business codes such as:
  - `PARAFUSO-M20`
  - `TONER-HP-CF281A`
  - `FURADEIRA-BOSCH-18V`
  - `ROLAMENTO-6205`
- Memory save triggers now support Portuguese and English:
  - `registre que`
  - `salve que`
  - `guarde que`
  - `lembre que`
  - `remember that`
  - `save that`
- Supplier facts are stored with a structured key:
  - `inventory:supplier:PARAFUSO-M20`
- Memory values are stored as JSON with:
  - `type`
  - `entity`
  - `value`
  - `original_text`
- Memory lookup now searches by:
  - full user query
  - product code
  - structured supplier key
  - structured inventory key
  - structured fact key
- The Inventory Agent prompt now explicitly prioritizes long-term memory for previously stored supplier, product, customer, order, and SKU-specific facts.

## Recommended regression test

Start the Inventory Agent:

```bash
uvicorn apps.inventory_agent.main:app --reload --port 8001
```

Open:

```text
http://localhost:8001/docs
```

Run these requests in order:

### 1. Save supplier memory

```json
{
  "operation": {},
  "messages": [
    {
      "type": "human",
      "content": "Registre que o fornecedor do PARAFUSO-M20 é XYZ Metais."
    }
  ],
  "trace_id": "teste-memory-v61-001"
}
```

### 2. Retrieve supplier memory

```json
{
  "operation": {},
  "messages": [
    {
      "type": "human",
      "content": "Qual o fornecedor do PARAFUSO-M20?"
    }
  ],
  "trace_id": "teste-memory-v61-002"
}
```

Expected answer:

```text
O fornecedor do PARAFUSO-M20 é XYZ Metais.
```

The exact wording may vary, but the response should mention `XYZ Metais`.
