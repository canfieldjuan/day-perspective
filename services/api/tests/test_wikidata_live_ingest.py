"""Ingesting a Wikidata entity that is not the pinned fixture (B2, #133).

The candidate pipeline was built around one offline entity: `Q749610` and its
revision were module constants, `_parse` refused anything else, and every claim
was stamped `date(1964, 3, 27)` regardless of what the entity said. That was
honest while exactly one entity existed; the stamp becomes a lie the moment a
second entity arrives -- its claims would carry the Alaska earthquake's date
while its own P585 said otherwise.

So the occurrence day is read from P585 through the shared resolver
(`_resolve_occurrence_date`), and an entity whose P585 is coarser than a day --
or in a calendar the resolver does not restate -- is refused rather than
approximated. Accepting sub-day precision is B3; this slice keeps it refused.

Nothing here touches the network. The fetcher is injected, so these tests
describe the contract a live fetch must satisfy without depending on Wikidata
being reachable or unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, PipelineRun, SourceRelease
from app.wikidata import (
    LocalFilesystemRawSourceStore,
    _optional,
    _value,
    ingest_wikidata_entity,
)

GREGORIAN = "http://www.wikidata.org/entity/Q1985727"


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
                    "calendarmodel": GREGORIAN,
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
    optional_properties: bool = True,
) -> bytes:
    """A structurally faithful Wikidata entity document.

    SYNTHETIC -- never published. It carries only the properties `_parse` reads,
    so a test can vary the entity and its date without shipping another fixture
    that could be mistaken for production data (S12).
    """
    claims: dict[str, Any] = {
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
    }
    entity: dict[str, Any] = {
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
        "claims": claims,
        "sitelinks": {},
    }
    if optional_properties:
        claims["P2527"] = [_snak("P2527", {"amount": "+7.1", "unit": "1"}, "quantity")]
        claims["P4511"] = [_snak("P4511", {"amount": "+10", "unit": "1"}, "quantity")]
        claims["P1120"] = [_snak("P1120", {"amount": "+3", "unit": "1"}, "quantity")]
    else:
        # An event that is not an earthquake has no magnitude, depth, fatality
        # count, or necessarily a coordinate.
        del claims["P625"]
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


# --- DB-free: statement selection for optional properties (shares B1) ---------


def _entity_with_p1120(statements: list[dict[str, Any]]) -> dict[str, Any]:
    return {"claims": {"P1120": statements}}


def test_optional_returns_none_for_an_absent_property() -> None:
    assert _optional({"claims": {}}, "P1120") is None


def test_optional_skips_a_deprecated_only_property() -> None:
    # The old statements[0] would have returned the deprecated value; the shared
    # selection excludes it, so an only-deprecated property yields no candidate.
    entity = _entity_with_p1120(
        [_snak("P1120", {"amount": "+999999", "unit": "1"}, "quantity")]
    )
    entity["claims"]["P1120"][0]["rank"] = "deprecated"
    assert _optional(entity, "P1120") is None


def test_optional_returns_the_best_ranked_value_statement() -> None:
    deprecated = _snak("P1120", {"amount": "+999999", "unit": "1"}, "quantity")
    deprecated["rank"] = "deprecated"
    normal = _snak("P1120", {"amount": "+3", "unit": "1"}, "quantity")
    chosen = _optional(_entity_with_p1120([deprecated, normal]), "P1120")
    assert chosen is not None
    assert _value(chosen) == {"amount": "+3", "unit": "1"}


# --- DB-free: the CLI refuses --revision with --fixture (the missing test) -----


def test_ingest_cli_refuses_revision_with_fixture(tmp_path: Path) -> None:
    """--revision pins a live fetch; the fixture carries its own revision.

    Passing both was silently ignored before; it is now refused so a caller who
    thinks they pinned a revision is told the fixture does not honour it.
    """
    # Imported inside the test: app.candidate_cli pulls in app.database, which
    # builds the engine at import; the refusal itself needs no database.
    from app.candidate_cli import _ingest

    args = argparse.Namespace(
        entity=None,
        fixture=tmp_path / "Q749610.json",
        revision=2497659168,
        dry_run=False,
    )
    settings = SimpleNamespace(raw_source_root=tmp_path / "raw")
    with pytest.raises(SystemExit, match="--revision applies to a live"):
        _ingest(args, settings, session=None)


# --- DB-bound: the live ingest contract (CI runs these) -----------------------


@pytest.mark.integration
def test_a_second_entity_carries_its_own_occurrence_date(
    session: Session, tmp_path: Path
) -> None:
    """The defect this arc exists to fix.

    Ingest stamped `date(1964, 3, 27)` on every claim. A San Francisco entity
    would have been filed under the Alaska earthquake's date -- a claim about
    when something happened, asserted by a constant rather than by evidence.
    """
    payload = entity_document(
        entity_id="Q108princ", revision_id=999001, occurrence="1906-04-18"
    )

    result = ingest_wikidata_entity(
        session,
        entity_id="Q108princ",
        revision_id=999001,
        fetcher=RecordingFetcher(payload, 999001),
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
    """Precision coarser than a day cannot place an event on a date."""
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
def test_a_sub_day_timestamp_is_refused_pending_B3(
    session: Session, tmp_path: Path
) -> None:
    """Finer than a day is refused for now.

    The shared resolver accepts only P585 precision 11 today; deriving a local
    civil day from a sub-day instant needs A3's coordinates->timezone and is
    B3's slice. Until then a sub-day P585 is refused, not silently truncated.
    """
    payload = entity_document(
        entity_id="Q108precise",
        revision_id=999011,
        occurrence="1969-07-20",
        precision=14,
    )

    with pytest.raises(ValueError, match="day-precise"):
        ingest_wikidata_entity(
            session,
            entity_id="Q108precise",
            revision_id=999011,
            fetcher=RecordingFetcher(payload, 999011),
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


@pytest.mark.integration
def test_an_event_without_earthquake_properties_can_be_ingested(
    session: Session, tmp_path: Path
) -> None:
    """Most of the Golden-100 is not an earthquake.

    `REQUIRED_EVENT_CLAIMS` asks only for identity, type, name and date;
    magnitude, depth, fatalities and even coordinates are optional. Requiring
    them at ingest would restrict live enrichment to earthquakes carrying a full
    measurement set.
    """
    payload = entity_document(
        entity_id="Q108treaty",
        revision_id=999010,
        occurrence="1919-06-28",
        optional_properties=False,
    )

    result = ingest_wikidata_entity(
        session,
        entity_id="Q108treaty",
        revision_id=999010,
        fetcher=RecordingFetcher(payload, 999010),
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )

    assert result.source_release_id is not None
    claim_types = {
        claim.claim_type
        for claim in session.scalars(
            select(Claim).where(Claim.source_release_id == result.source_release_id)
        )
    }
    for required in (
        "candidate_event_identity",
        "candidate_event_type",
        "candidate_name",
        "candidate_occurrence_date",
    ):
        assert required in claim_types
    # Absent properties produce no claim rather than a null-valued one: the
    # payload should not assert a magnitude the entity never stated.
    assert "candidate_magnitude" not in claim_types
    assert "candidate_coordinates" not in claim_types


@pytest.mark.integration
def test_a_failed_live_fetch_still_leaves_an_audit_trail(
    session: Session, tmp_path: Path
) -> None:
    """DNS, timeout and HTTP errors are the common live failures.

    The CLI commits ingestion failures specifically so the failed run survives.
    A fetch that raised before the run existed left nothing to commit, so the
    most likely failure mode was the one with no record.
    """

    class FailingFetcher:
        def fetch(self, entity_id: str, revision_id: int | None) -> tuple[bytes, int]:
            raise TimeoutError("the network is down")

    before = len(list(session.scalars(select(PipelineRun))))

    with pytest.raises(TimeoutError):
        ingest_wikidata_entity(
            session,
            entity_id="Q108princ",
            revision_id=999012,
            fetcher=FailingFetcher(),
            raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
        )

    runs = list(session.scalars(select(PipelineRun)))
    assert len(runs) == before + 1, "the failed fetch recorded no pipeline run"
    run = runs[-1]
    assert run.status == "failed"
    assert "the network is down" in json.dumps(run.details)
