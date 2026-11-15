from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import wikidata
from app.adapters.base import LocalFilesystemRawSourceStore
from app.config import get_settings
from app.database import get_session
from app.main import app
from app.models import Event, EventLocation

from .test_usgs_vertical_slice import override_session
from .test_usgs_vertical_slice import publish as publish_usgs
from .test_wikidata_live_ingest import RecordingFetcher, entity_document

PREFIX = "/api/v1/admin/wikidata/1964-03-27"


def test_selected_date_review_requires_the_development_token() -> None:
    assert TestClient(app).get(PREFIX).status_code == 403


@pytest.mark.integration
def test_selected_release_review_and_resolve(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usgs, _ = publish_usgs(session, tmp_path)
    settings = get_settings()
    published = tmp_path / "published"
    monkeypatch.setattr(settings, "published_profile_root", published)
    monkeypatch.setattr(app, "dependency_overrides", {get_session: override_session(session)})
    raw = LocalFilesystemRawSourceStore(tmp_path / "raw")
    first = wikidata.ingest_wikidata_candidate(
        session,
        fixture_path=Path(__file__).resolve().parents[3] / "data/fixtures/wikidata/Q749610.json",
        raw_store=raw,
    ).source_release_id
    document = entity_document(
        entity_id="Q910000002", revision_id=1, occurrence="1964-03-27", optional_properties=False
    )
    second = wikidata.ingest_wikidata_entity(
        session,
        entity_id="Q910000002",
        revision_id=1,
        fetcher=RecordingFetcher(document, 1),
        raw_store=raw,
    ).source_release_id
    assert first is not None and second is not None
    session.commit()
    client = TestClient(
        app, headers={"X-Development-Review-Token": settings.development_review_token}
    )
    path = f"{PREFIX}/{first}"

    def read() -> dict[UUID, dict[str, Any]]:
        response = client.get(PREFIX)
        assert response.status_code == 200, response.text
        return {UUID(row["release_id"]): row for row in response.json()["candidates"]}

    rows = read()
    assert set(rows) == {first, second}
    assert rows[first]["entity_id"] == "Q749610"
    assert rows[first]["revision"] and rows[first]["checksum"]
    assert rows[first]["source_url"].startswith("https://www.wikidata.org/")
    assert client.get(PREFIX.replace("1964-03-27", "2026-01-01")).status_code == 404
    assert client.get(PREFIX.replace("1964-03-27", "1964-03-28")).json() == {"candidates": []}
    assert client.post(f"{PREFIX}/{usgs.source_release_id}/resolve").status_code == 404
    assert client.post(f"{PREFIX}/{uuid4()}/resolve").status_code == 404
    assert client.post(f"{path}/resolve").status_code == 409

    choices = {claim["predicate"]: claim for claim in rows[first]["claims"]}
    name = choices["candidate_name"]
    assert name["value"]["value"]["label"] and name["locator"]
    other = rows[second]["claims"][0]["id"]
    approval = {"decision": "accepted", "rationale": "Checked the offline test evidence"}
    wrong_day = path.replace("1964-03-27", "1964-03-28")
    assert (
        client.post(f"{wrong_day}/claims/{name['id']}/decision", json=approval).status_code == 409
    )
    assert client.post(f"{path}/claims/{other}/decision", json=approval).status_code == 404
    assert (
        client.post(
            f"{path}/claims/{name['id']}/decision", json={**approval, "rationale": " "}
        ).status_code
        == 422
    )
    assert read()[first]["claims"] == rows[first]["claims"]
    for predicate in wikidata.REQUIRED_EVENT_CLAIMS:
        response = client.post(f"{path}/claims/{choices[predicate]['id']}/decision", json=approval)
        assert response.status_code == 200, response.text
    assert client.post(f"{path}/claims/{name['id']}/decision", json=approval).status_code == 409
    coordinates = choices["candidate_coordinates"]
    assert (
        client.post(
            f"{path}/claims/{coordinates['id']}/decision",
            json={
                "decision": "rejected",
                "rationale": "Withhold optional location in this fixture",
            },
        ).status_code
        == 200
    )
    assert client.post(f"{path}/resolve").status_code == 200
    assert read()[first]["resolved"]
    event = session.scalars(select(Event).where(Event.event_type == "Q7944")).one()
    assert event.canonical_title == name["value"]["value"]["label"]
    assert (
        session.scalar(
            select(func.count())
            .select_from(EventLocation)
            .where(EventLocation.event_id == event.id)
        )
        == 0
    )
    assert read()[second] == rows[second]
    revision = entity_document(
        entity_id="Q749610", revision_id=2, occurrence="1964-03-27", optional_properties=False
    )
    conflicting = wikidata.ingest_wikidata_entity(
        session,
        entity_id="Q749610",
        revision_id=2,
        fetcher=RecordingFetcher(revision, 2),
        raw_store=raw,
    ).source_release_id
    assert conflicting is not None
    session.commit()
    conflict = read()[conflicting]
    assert "revision reconciliation" in conflict["blocked"]
    assert not conflict["can_resolve"] and not conflict["resolved"]
