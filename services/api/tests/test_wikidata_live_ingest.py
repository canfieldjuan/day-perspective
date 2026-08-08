"""Ingesting an entity that is not the pinned fixture (G4-0, issue #106).

The candidate pipeline was built around one offline entity: `Q749610` and its
revision are module constants, `_parse` refuses anything else, and every claim
is stamped `date(1964, 3, 27)` regardless of what the entity says. That was
honest while exactly one entity existed. G4 needs the other 99 dates, and the
stamp becomes a lie the moment a second entity arrives — its claims would carry
the Alaska earthquake's date while its own P585 said otherwise.

So the occurrence date is read from P585, the same value `_parse_occurrence_date`
already calls "honest for any entity", and an entity whose P585 is coarser than a
day is refused rather than approximated. This arc is date-specific enrichment; a
date we had to round is not a date we can publish against.

Nothing here touches the network. The fetcher is injected, so these tests
describe the contract a live fetch must satisfy without depending on Wikidata
being reachable or unchanged.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, PipelineRun, SourceRelease
from app.wikidata import (
    LocalFilesystemRawSourceStore,
    ingest_wikidata_entity,
)


def _time_statement(iso_day: str, precision: int = 11) -> dict[str, Any]:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": "P585",
            "datavalue": {
                "value": {
                    "time": f"+{iso_day}T00:00:00Z",
                    "timezone": 0,
                    "before": 0,
                    "after": 0,
                    "precision": precision,
                    "calendarmodel": "http://www.wikidata.org/entity/Q1985727",
                },
                "type": "time",
            },
            "datatype": "time",
        },
        "type": "statement",
        "rank": "normal",
    }


def _snak(property_id: str, value: Any, datatype: str = "wikibase-item") -> dict[str, Any]:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": property_id,
            "datavalue": {"value": value, "type": "string"},
            "datatype": datatype,
        },
        "type": "statement",
        "rank": "normal",
    }


def entity_document(
    *,
    entity_id: str,
    revision_id: int,
    occurrence: str,
    precision: int = 11,
    label: str = "A synthetic recorded event",
) -> bytes:
    """A structurally faithful Wikidata entity document.

    SYNTHETIC — never published. It carries only the properties `_parse` reads,
    so a test can vary the entity and its date without shipping another fixture
    that could be mistaken for production data (§12).
    """
    entity = {
        "type": "item",
        "id": entity_id,
        "pageid": 4242,
        "ns": 0,
        "title": entity_id,
        "lastrevid": revision_id,
        "modified": "2026-01-01T00:00:00Z",
        "labels": {"en": {"language": "en", "value": label}},
        "descriptions": {},
        "aliases": {"en": [{"language": "en", "value": f"{label} (alias)"}]},
        "claims": {
            "P31": [_snak("P31", {"entity-type": "item", "id": "Q7944"})],
            "P585": [_time_statement(occurrence, precision)],
            "P625": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P625",
                        "datavalue": {
                            "value": {
                                "latitude": 1.5,
                                "longitude": 2.5,
                                "precision": 0.001,
                                "globe": "http://www.wikidata.org/entity/Q2",
                            },
                            "type": "globecoordinate",
                        },
                        "datatype": "globe-coordinate",
                    },
                    "type": "statement",
                    "rank": "normal",
                }
            ],
            "P2527": [_snak("P2527", {"amount": "+7.1", "unit": "1"}, "quantity")],
            "P4511": [_snak("P4511", {"amount": "+10", "unit": "1"}, "quantity")],
            "P1120": [_snak("P1120", {"amount": "+3", "unit": "1"}, "quantity")],
        },
        "sitelinks": {},
    }
    return json.dumps({"entities": {entity_id: entity}}).encode("utf-8")


class RecordingFetcher:
    """Stands in for the network, and records what was asked of it."""

    def __init__(self, payload: bytes, resolved_revision: int) -> None:
        self.payload = payload
        self.resolved_revision = resolved_revision
        self.calls: list[tuple[str, int | None]] = []

    def fetch(self, entity_id: str, revision_id: int | None) -> tuple[bytes, int]:
        self.calls.append((entity_id, revision_id))
        return self.payload, self.resolved_revision


@pytest.mark.integration
def test_a_second_entity_carries_its_own_occurrence_date(
    session: Session, tmp_path: Path
) -> None:
    """The defect this slice exists to fix.

    Ingest stamped `date(1964, 3, 27)` on every claim. A San Francisco entity
    would have been filed under the Alaska earthquake's date — a claim about
    when something happened, asserted by a constant rather than by evidence.
    """
    payload = entity_document(
        entity_id="Q108princ", revision_id=999001, occurrence="1906-04-18"
    )
    fetcher = RecordingFetcher(payload, 999001)

    result = ingest_wikidata_entity(
        session,
        entity_id="Q108princ",
        revision_id=999001,
        fetcher=fetcher,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )

    assert result.source_release_id is not None
    claims = list(
        session.scalars(
            select(Claim).where(Claim.source_release_id == result.source_release_id)
        )
    )
    assert claims, "ingest recorded no claims"
    for claim in claims:
        assert claim.temporal_start == date(1906, 4, 18), (
            f"{claim.claim_type} carries {claim.temporal_start}, not the "
            "entity's own P585 date"
        )
        assert claim.temporal_end == date(1906, 4, 18)


@pytest.mark.integration
def test_the_release_records_the_entity_and_revision_actually_fetched(
    session: Session, tmp_path: Path
) -> None:
    """Provenance names the real entity, and does not claim to be a fixture."""
    payload = entity_document(
        entity_id="Q108princ", revision_id=999002, occurrence="1906-04-18"
    )

    result = ingest_wikidata_entity(
        session,
        entity_id="Q108princ",
        revision_id=999002,
        fetcher=RecordingFetcher(payload, 999002),
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )

    release = session.get(SourceRelease, result.source_release_id)
    assert release is not None
    metadata = release.metadata_json or {}
    assert metadata.get("entity_id") == "Q108princ"
    assert metadata.get("revision_id") == 999002
    # A live release must not describe itself as a pinned fixture.
    assert metadata.get("fixture") in (None, False)
    assert "Q749610" not in json.dumps(metadata)
    # The checksum is of the bytes that were actually fetched.
    assert release.raw_checksum_sha256 == hashlib.sha256(payload).hexdigest()

    run = session.get(PipelineRun, result.pipeline_run_id)
    assert run is not None
    assert run.details.get("mode") == "live"


@pytest.mark.integration
def test_an_unpinned_revision_records_what_was_served(
    session: Session, tmp_path: Path
) -> None:
    """Omitting --revision must still produce an auditable artifact.

    "Latest" as an unrecorded moving target would make the release
    irreproducible; the entity's own lastrevid is recorded instead.
    """
    payload = entity_document(
        entity_id="Q108princ", revision_id=999003, occurrence="1906-04-18"
    )
    fetcher = RecordingFetcher(payload, 999003)

    result = ingest_wikidata_entity(
        session,
        entity_id="Q108princ",
        revision_id=None,
        fetcher=fetcher,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )

    assert fetcher.calls == [("Q108princ", None)]
    release = session.get(SourceRelease, result.source_release_id)
    assert release is not None
    assert (release.metadata_json or {}).get("revision_id") == 999003


@pytest.mark.integration
def test_a_date_we_would_have_to_round_is_refused(
    session: Session, tmp_path: Path
) -> None:
    """Precision coarser than a day cannot place an event on a date.

    Approximating it would put a statement on a page that says "on this date"
    about something we only know the month of.
    """
    payload = entity_document(
        entity_id="Q108princ",
        revision_id=999004,
        occurrence="1906-04-01",
        precision=10,
    )

    with pytest.raises(ValueError, match="day-precise"):
        ingest_wikidata_entity(
            session,
            entity_id="Q108princ",
            revision_id=999004,
            fetcher=RecordingFetcher(payload, 999004),
            raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
        )


@pytest.mark.integration
def test_a_served_entity_that_is_not_the_one_requested_is_refused(
    session: Session, tmp_path: Path
) -> None:
    """The payload must be the entity we asked for.

    Otherwise a redirect or a mistaken id silently files one event's evidence
    under another's identity.
    """
    payload = entity_document(
        entity_id="Q999other", revision_id=999005, occurrence="1906-04-18"
    )

    with pytest.raises(ValueError):
        ingest_wikidata_entity(
            session,
            entity_id="Q108princ",
            revision_id=999005,
            fetcher=RecordingFetcher(payload, 999005),
            raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
        )


@pytest.mark.integration
def test_ingesting_the_same_entity_twice_is_idempotent(
    session: Session, tmp_path: Path
) -> None:
    payload = entity_document(
        entity_id="Q108princ", revision_id=999006, occurrence="1906-04-18"
    )
    store = LocalFilesystemRawSourceStore(tmp_path / "raw")

    first = ingest_wikidata_entity(
        session,
        entity_id="Q108princ",
        revision_id=999006,
        fetcher=RecordingFetcher(payload, 999006),
        raw_store=store,
    )
    second = ingest_wikidata_entity(
        session,
        entity_id="Q108princ",
        revision_id=999006,
        fetcher=RecordingFetcher(payload, 999006),
        raw_store=store,
    )

    assert second.idempotent is True
    assert second.source_release_id == first.source_release_id


@pytest.mark.integration
def test_a_live_dry_run_writes_nothing(session: Session, tmp_path: Path) -> None:
    payload = entity_document(
        entity_id="Q108princ", revision_id=999007, occurrence="1906-04-18"
    )
    before = len(list(session.scalars(select(SourceRelease))))

    result = ingest_wikidata_entity(
        session,
        entity_id="Q108princ",
        revision_id=999007,
        fetcher=RecordingFetcher(payload, 999007),
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.source_release_id is None
    assert len(list(session.scalars(select(SourceRelease)))) == before


@pytest.mark.integration
def test_the_license_record_names_the_entity_it_covers(
    session: Session, tmp_path: Path
) -> None:
    """Attribution is provenance, so it must not name a different entity.

    CC0 does not require attribution; this project records it anyway. A record
    that credits `Q749610` for a San Francisco entity's data is a false
    provenance claim, and one that survives into the licensing audit trail.
    """
    from app.governance import SourceReleaseLicense

    payload = entity_document(
        entity_id="Q108princ", revision_id=999008, occurrence="1906-04-18"
    )

    result = ingest_wikidata_entity(
        session,
        entity_id="Q108princ",
        revision_id=999008,
        fetcher=RecordingFetcher(payload, 999008),
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )

    license_row = session.scalar(
        select(SourceReleaseLicense).where(
            SourceReleaseLicense.source_release_id == result.source_release_id
        )
    )
    assert license_row is not None
    assert "Q108princ" in (license_row.attribution_text or "")
    assert "Q749610" not in (license_row.attribution_text or "")
