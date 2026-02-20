from app.api.v1.routers.chat import ChatMessage, _build_retrieval_query


def test_build_retrieval_query_includes_history() -> None:
    query = _build_retrieval_query(
        "Y que dice de la clausula 8?",
        history=[
            ChatMessage(role="user", content="Hablemos de ISO 9001"),
            ChatMessage(role="assistant", content="Perfecto, enfocamos en esa norma."),
        ],
    )

    assert "HISTORIAL RELEVANTE" in query
    assert "ISO 9001" in query
    assert "PREGUNTA ACTUAL" in query


def test_build_retrieval_query_limits_turns_and_skips_empty() -> None:
    query = _build_retrieval_query(
        "pregunta actual",
        history=[
            ChatMessage(role="user", content="a"),
            ChatMessage(role="assistant", content="b"),
            ChatMessage(role="user", content=""),
            ChatMessage(role="assistant", content="c"),
        ],
        max_turns=2,
    )

    assert "USER: a" not in query
    assert "ASSISTANT: b" in query
    assert "ASSISTANT: c" in query
