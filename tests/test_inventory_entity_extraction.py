import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("LLM_PROVIDER", "openai")

from apps.inventory_agent.main import (  # noqa: E402
    build_memory_key,
    extract_preferred_supplier_fact,
    extract_product_codes,
    extract_sku,
    is_memory_save_request,
)


def test_extract_business_product_code():
    assert extract_sku("Qual o fornecedor do PARAFUSO-M20?") == "PARAFUSO-M20"
    assert extract_product_codes("Consultar TONER-HP-CF281A e FURADEIRA-BOSCH-18V") == [
        "TONER-HP-CF281A",
        "FURADEIRA-BOSCH-18V",
    ]


def test_portuguese_memory_trigger():
    assert is_memory_save_request("Registre que o fornecedor do PARAFUSO-M20 é XYZ Metais.")
    assert is_memory_save_request("Guarde que o estoque mínimo do PARAFUSO-M20 é 50 unidades.")


def test_supplier_fact_key():
    fact = extract_preferred_supplier_fact("Registre que o fornecedor do PARAFUSO-M20 é XYZ Metais.")
    assert fact == ("PARAFUSO-M20", "XYZ Metais")
    assert build_memory_key("Registre que o fornecedor do PARAFUSO-M20 é XYZ Metais.") == "inventory:supplier:PARAFUSO-M20"
