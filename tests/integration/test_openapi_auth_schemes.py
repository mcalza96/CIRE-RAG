from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_chat_completion_exposes_auth_security_schemes() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()

    post_operation = schema["paths"]["/api/v1/chat/completions"]["post"]
    security = post_operation.get("security") or []
    scheme_names = {name for item in security for name in item.keys()}

    assert "BearerAuth" in scheme_names
    assert "ServiceSecretAuth" in scheme_names

