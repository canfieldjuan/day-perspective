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
from app.governance import EditorialSelection, IdentityAdjudicationDecision
from app.main import app
from app.models import PublicationManifest, PublicationStatementEvidence
from app.services import LocalFilesystemPublishedProfileStore

from .test_usgs_vertical_slice import override_session
from .test_usgs_vertical_slice import publish as publish_usgs
from .test_wikidata_live_ingest import RecordingFetcher, entity_document

PREFIX = "/api/v1/admin/wikidata/1964-03-27"


def test_selected_date_review_requires_the_development_token() -> None:
    assert TestClient(app).get(PREFIX).status_code == 403


@pytest.mark.integration
def test_selected_release_review_resolve_select_and_publish(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usgs, golden = publish_usgs(session, tmp_path)
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
    assert client.post(f"{path}/publish").status_code == 409

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
    candidate = read()[first]
    assert [item["predicate"] for item in candidate["preview"]] == [
        "candidate_name",
        "candidate_event_type",
        "candidate_occurrence_date",
    ]
    assert all(item["version"] == 0 for item in candidate["preview"])
    assert client.post(f"{path}/publish").status_code == 409
    preview = [{"root_id": item["root_id"], "version": 0} for item in candidate["preview"]]
    selection = {"rationale": "Select the three reviewed statements", "preview": preview}
    assert (
        client.post(f"{path}/select", json={**selection, "preview": preview[::-1]}).status_code
        == 409
    )
    assert client.post(f"{path}/select", json=selection).status_code == 200
    mixed = [{**preview[0], "version": 1}, *preview[1:]]
    assert (
        client.post(
            f"{path}/select", json={"rationale": "A stale edit", "preview": mixed}
        ).status_code
        == 409
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(EditorialSelection)
            .where(EditorialSelection.rationale == "A stale edit")
        )
        == 0
    )
    assert read()[first]["can_publish"]
    assert client.post(f"{path}/publish").json()["status"] == "deferred_to_merge_review"
    wikidata.resolve_merge_review(
        session,
        source_release_id=first,
        decision=IdentityAdjudicationDecision.DISTINCT_EVENT,
        reviewer="test-human",
        rationale="Exercise retention of an admitted event in the fixture",
    )
    session.commit()
    response = client.post(f"{path}/publish")
    assert response.status_code == 200, response.text
    outcome = response.json()
    assert outcome["status"] == "published"
    assert client.post(f"{path}/publish").json()["manifest_id"] == outcome["manifest_id"]
    public = client.get("/api/v1/day/1964-03-27").json()
    assert public["manifest_id"] == outcome["manifest_id"]
    statements = public["profile"]["sections"]["recorded_on_this_date"]
    prior = session.get(PublicationManifest, golden.publication_manifest_id)
    assert prior is not None
    old = LocalFilesystemPublishedProfileStore(published).read(
        prior.storage_uri, prior.content_hash
    )
    assert {item["statement"] for item in old["sections"]["recorded_on_this_date"]} <= {
        item["statement"] for item in statements
    }
    assert any(name["value"]["value"]["label"] in item["statement"] for item in statements)
    assert not any(item["statement_id"] == "wikidata-coordinates" for item in statements)
    assert session.scalar(
        select(func.count())
        .select_from(PublicationStatementEvidence)
        .where(PublicationStatementEvidence.publication_manifest_id == UUID(outcome["manifest_id"]))
    ) == sum(len(items) for items in public["profile"]["sections"].values())
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
    assert not conflict["can_resolve"] and not conflict["can_publish"]
