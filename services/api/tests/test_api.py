from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import get_session
from app.main import app


@pytest.mark.integration
def test_api_returns_profile_not_published_for_an_unpublished_supported_day(session: Session) -> None:
    def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).get("/api/v1/day/1969-07-20")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404
    assert response.json() == {
        "status": "profile_not_published",
        "date": "1969-07-20",
        "profile_type": "standard_statistical",
        "detail": "No profile has been published for this date yet.",
    }
