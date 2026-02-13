from app.api.v1.routers.chat import ChatMessage, _build_retrieval_query
from app.services.knowledge.grounded_answer_service import GroundedAnswerService


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


def test_render_history_limits_turns_and_skips_empty() -> None:
    rendered = GroundedAnswerService._render_history(
        [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "c"},
        ],
        max_turns=2,
    )

    assert "USER: a" not in rendered
    assert "ASSISTANT: b" not in rendered
    assert "ASSISTANT: c" in rendered
