from app.mas_simple.domain.policies import build_retrieval_plan, classify_intent


def test_classify_intent_literal_lista():
    intent = classify_intent("Lista las entradas exclusivas de la cláusula 9.3")
    assert intent.mode == "literal_lista"


def test_classify_intent_comparativa():
    intent = classify_intent("Compara ISO 27001 vs ISO 9001 para proveedores externos")
    assert intent.mode == "comparativa"


def test_build_retrieval_plan_literal_is_strict():
    intent = classify_intent("Que documento obligatorio exige la clausula 6.1.3")
    plan = build_retrieval_plan(intent)
    assert plan.require_literal_evidence is True
    assert plan.chunk_k >= 40
    assert plan.summary_k <= 3
