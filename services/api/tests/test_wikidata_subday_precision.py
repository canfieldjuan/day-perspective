"""Second-precision P585 is placed on its local civil day (B3, #133).

A day-precision P585 is a stated local civil day, taken as reported. A
second-precision P585 is an instant: its local civil day is DERIVED via the
coordinates' timezone (A3) and the shared resolver, with D013 provenance -- not
truncated to its UTC date. Hour/minute precision (12/13), a sub-day instant with
no reviewed place, and coordinates no boundary covers stay refused (the operator
scoped B3 to second precision).

All DB-bound: sub-day derivation reads the PostGIS timezone-boundary table, which
`seed_test_timezones` fills from the committed mini fixture (a Europe/Berlin box
at lon 10-11, lat 50-51; Germany kept no DST in 1969, so CET is UTC+1, and an
instant at 23:30Z there falls on the next local day).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.governance import ReviewDecisionValue, record_claim_review
from app.models import Claim, EventTime, TemporalAssignment, TemporalPrecision
from app.timezone_boundaries import load_timezone_boundaries
from app.wikidata import (
    LocalFilesystemRawSourceStore,
    _persisted_occurrence,
    _recorded_statement_text,
    ingest_wikidata_entity,
    resolve_wikidata_event,
)
from tests.helpers import seed_test_timezones

GREGORIAN = "http://www.wikidata.org/entity/Q1985727"
# Inside the mini fixture's Europe/Berlin box (lon 10-11, lat 50-51).
BERLIN_LAT = 50.5
BERLIN_LON = 10.5
CORE_CLAIMS = (
    "candidate_event_identity",
    "candidate_event_type",
    "candidate_name",
    "candidate_occurrence_date",
    "candidate_coordinates",
)


def _p585(iso_timestamp: str, precision: int) -> dict[str, Any]:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": "P585",
            "datavalue": {
                "value": {
                    "time": f"+{iso_timestamp}",
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


def _p625(
    latitude: float, longitude: float, globe: str = "http://www.wikidata.org/entity/Q2"
) -> dict[str, Any]:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": "P625",
            "datavalue": {
                "value": {
                    "latitude": latitude,
                    "longitude": longitude,
                    "precision": 0.0001,
                    "globe": globe,
                },
                "type": "globecoordinate",
            },
            "datatype": "globe-coordinate",
        },
        "type": "statement",
        "rank": "normal",
    }


def _p31() -> dict[str, Any]:
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": "P31",
            "datavalue": {
                "value": {"entity-type": "item", "id": "Q7944"},
                "type": "wikibase-entityid",
            },
            "datatype": "wikibase-item",
        },
        "type": "statement",
        "rank": "normal",
    }


def _entity_document(
    *,
    entity_id: str,
    revision_id: int,
    timestamp: str,
    precision: int,
    latitude: float = BERLIN_LAT,
    longitude: float = BERLIN_LON,
    globe: str = "http://www.wikidata.org/entity/Q2",
    with_coordinates: bool = True,
) -> bytes:
    """A structurally faithful, synthetic Wikidata entity document (§12: test-only)."""
    claims: dict[str, Any] = {
        "P31": [_p31()],
        "P585": [_p585(timestamp, precision)],
    }
    if with_coordinates:
        claims["P625"] = [_p625(latitude, longitude, globe)]
    entity = {
        "type": "item",
        "id": entity_id,
        "pageid": 4242,
        "ns": 0,
        "title": entity_id,
        "lastrevid": revision_id,
        "modified": "2026-01-01T00:00:00Z",
        "labels": {"en": {"language": "en", "value": "A synthetic recorded event"}},
        "descriptions": {},
        "aliases": {"en": [{"language": "en", "value": "synthetic (alias)"}]},
        "claims": claims,
        "sitelinks": {},
    }
    return json.dumps({"entities": {entity_id: entity}}).encode("utf-8")


class _Fetcher:
    def __init__(self, payload: bytes, revision: int) -> None:
        self._payload = payload
        self._revision = revision

    def fetch(self, entity_id: str, revision_id: int | None) -> tuple[bytes, int]:
        return self._payload, self._revision


def _ingest(session: Session, payload: bytes, revision: int, tmp_path: Path) -> None:
    ingest_wikidata_entity(
        session,
        entity_id="Q108subday",
        revision_id=revision,
        fetcher=_Fetcher(payload, revision),
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )


def _accept_core(session: Session) -> None:
    for claim_type in CORE_CLAIMS:
        claim = session.scalars(
            select(Claim).where(Claim.claim_type == claim_type)
        ).one()
        record_claim_review(
            session,
            claim=claim,
            decision=ReviewDecisionValue.ACCEPTED,
            rationale="Human-reviewed Wikidata candidate for this test.",
            reviewed_by="test-human",
        )


@pytest.mark.integration
def test_second_precision_p585_is_derived_to_its_local_civil_day(
    session: Session, tmp_path: Path
) -> None:
    seed_test_timezones(session)
    # 23:30 UTC in Berlin (UTC+1 in 1969) is 00:30 the next local day: the derived
    # day is the 21st, not the UTC date of the 20th.
    payload = _entity_document(
        entity_id="Q108subday",
        revision_id=700001,
        timestamp="1969-07-20T23:30:00Z",
        precision=14,
    )
    _ingest(session, payload, 700001, tmp_path)
    _accept_core(session)

    event = resolve_wikidata_event(session)
    event_time = session.scalars(
        select(EventTime).where(
            EventTime.event_id == event.id, EventTime.is_primary.is_(True)
        )
    ).one()

    assert event_time.start_date == date(1969, 7, 21)
    assert event_time.end_date == date(1969, 7, 21)
    assert event_time.temporal_precision is TemporalPrecision.SECOND
    assert event_time.temporal_assignment is TemporalAssignment.DIRECT_RECORD
    assert event_time.exact_timestamp == datetime(1969, 7, 20, 23, 30, tzinfo=UTC)
    assert event_time.local_date == date(1969, 7, 21)
    assert event_time.timezone_name == "Europe/Berlin"
    assert event_time.utc_offset_minutes == 60
    # The derived day cites its place evidence (the coordinates), not nothing.
    assert event_time.local_date_provenance_resolved_claim_id is not None

    # The occurrence CLAIM (frozen into immutable publication evidence) must
    # agree with the EventTime: SECOND / DIRECT_RECORD, not DAY / REPORTED, and
    # carry the derived provenance including the boundary dataset version so the
    # day is reproducible after a reseed.
    occurrence_claim = session.scalars(
        select(Claim).where(Claim.claim_type == "candidate_occurrence_date")
    ).one()
    assert occurrence_claim.temporal_precision is TemporalPrecision.SECOND
    assert occurrence_claim.temporal_assignment is TemporalAssignment.DIRECT_RECORD
    assert occurrence_claim.temporal_start == date(1969, 7, 21)
    derived = (occurrence_claim.assertion_json or {}).get("derived_local_date")
    assert derived is not None
    assert derived["date"] == "1969-07-21"
    assert derived["timezone"] == "Europe/Berlin"
    assert derived["utc_offset_minutes"] == 60
    assert derived["timezone_dataset_version"] == "mini-test"
    assert derived["instant"] == "1969-07-20T23:30:00+00:00"


@pytest.mark.integration
def test_day_precision_live_entity_stays_reported_without_derived_fields(
    session: Session, tmp_path: Path
) -> None:
    # A day-precision instant is reported: no timezone seeding is consulted, and
    # the five D013 fields stay empty, exactly as the reported path always left
    # them. This locks that B3 changed only the sub-day branch.
    payload = _entity_document(
        entity_id="Q108subday",
        revision_id=700002,
        timestamp="1969-07-20T00:00:00Z",
        precision=11,
    )
    _ingest(session, payload, 700002, tmp_path)
    _accept_core(session)

    event = resolve_wikidata_event(session)
    event_time = session.scalars(
        select(EventTime).where(
            EventTime.event_id == event.id, EventTime.is_primary.is_(True)
        )
    ).one()

    assert event_time.start_date == date(1969, 7, 20)
    assert event_time.temporal_precision is TemporalPrecision.DAY
    assert event_time.temporal_assignment is TemporalAssignment.REPORTED
    assert event_time.exact_timestamp is None
    assert event_time.local_date is None
    assert event_time.timezone_name is None
    assert event_time.utc_offset_minutes is None
    assert event_time.local_date_provenance_resolved_claim_id is None


@pytest.mark.integration
def test_second_precision_without_coordinates_is_refused(
    session: Session, tmp_path: Path
) -> None:
    seed_test_timezones(session)
    payload = _entity_document(
        entity_id="Q108subday",
        revision_id=700003,
        timestamp="1969-07-20T23:30:00Z",
        precision=14,
        with_coordinates=False,
    )
    with pytest.raises(ValueError, match="no coordinates"):
        _ingest(session, payload, 700003, tmp_path)


@pytest.mark.integration
def test_second_precision_coordinates_no_boundary_covers_is_refused(
    session: Session, tmp_path: Path
) -> None:
    seed_test_timezones(session)
    # The Gulf of Guinea (0N, 0E) is in no mini-fixture boundary.
    payload = _entity_document(
        entity_id="Q108subday",
        revision_id=700004,
        timestamp="1969-07-20T23:30:00Z",
        precision=14,
        latitude=0.0,
        longitude=0.0,
    )
    with pytest.raises(ValueError, match="[Nn]o timezone boundary covers"):
        _ingest(session, payload, 700004, tmp_path)


@pytest.mark.integration
def test_hour_and_minute_precision_are_refused(
    session: Session, tmp_path: Path
) -> None:
    # The operator scoped B3 to second precision: TemporalPrecision has no HOUR
    # or MINUTE, so 12/13 have no honest precision to record and stay refused,
    # tracked in #133 rather than mapped to SECOND (which would overstate).
    seed_test_timezones(session)
    payload = _entity_document(
        entity_id="Q108subday",
        revision_id=700005,
        timestamp="1969-07-20T23:00:00Z",
        precision=13,
    )
    with pytest.raises(ValueError, match="precision 11 or 14"):
        _ingest(session, payload, 700005, tmp_path)


@pytest.mark.integration
def test_second_precision_non_earth_coordinates_are_refused(
    session: Session, tmp_path: Path
) -> None:
    # P625 on another globe (here the Moon, Q405) must not be read against Earth
    # timezone polygons even if its numeric point lands inside one; it fails closed.
    seed_test_timezones(session)
    payload = _entity_document(
        entity_id="Q108subday",
        revision_id=700006,
        timestamp="1969-07-20T23:30:00Z",
        precision=14,
        globe="http://www.wikidata.org/entity/Q405",
    )
    with pytest.raises(ValueError, match="not on Earth"):
        _ingest(session, payload, 700006, tmp_path)


def test_derived_occurrence_statement_does_not_attribute_the_local_day_to_wikidata() -> (
    None
):
    """§12: the derived local day is the product's conversion, not Wikidata's claim.

    DB-free. For a second-precision instant the rendered statement attributes the
    INSTANT to Wikidata and states the local-day conversion as the product's,
    never 'Wikidata records the occurrence on {derived day}'.
    """
    # Nonzero seconds: the SECOND-precision instant must render its seconds, not
    # be truncated to the minute (which would understate the classified precision).
    text = _recorded_statement_text(
        "candidate_occurrence_date",
        value={},
        occurrence_date=date(1969, 7, 21),
        occurrence_instant=datetime(1969, 7, 20, 23, 30, 45, tzinfo=UTC),
        occurrence_timezone="Europe/Berlin",
    )
    assert "July 20, 1969 23:30:45 UTC" in text  # the instant, attributed to Wikidata
    assert "Europe/Berlin civil time that is July 21, 1969" in text
    assert "records the occurrence on July 21" not in text


def test_reported_occurrence_statement_reads_as_wikidatas_stated_day() -> None:
    """DB-free. A day-precision (reported) occurrence still reads as the stated day."""
    text = _recorded_statement_text(
        "candidate_occurrence_date",
        value={},
        occurrence_date=date(1964, 3, 27),
    )
    assert text == "Wikidata records the occurrence on March 27, 1964."


def test_persisted_occurrence_rebuilds_purely_from_the_recorded_block() -> None:
    """DB-free. Reconstruction reads the recorded values verbatim -- no resolve_day,
    no tzdata, no boundary lookup -- so neither a boundary reseed nor a tzdata
    correction can make it drift from the immutable claim snapshot.
    """
    from types import SimpleNamespace

    claim = SimpleNamespace(
        assertion_json={
            "value": {"time": "+1969-07-20T23:30:00Z", "precision": 14},
            "derived_local_date": {
                "date": "1969-07-21",
                "timezone": "Europe/Berlin",
                "utc_offset_minutes": 60,
                "timezone_dataset_version": "mini-test",
                "instant": "1969-07-20T23:30:00+00:00",
                "interpretation": "derived under Europe/Berlin rules",
            },
        }
    )
    resolution = _persisted_occurrence(cast(Claim, claim))
    assert resolution.profile_date == date(1969, 7, 21)
    assert resolution.temporal_precision is TemporalPrecision.SECOND
    assert resolution.temporal_assignment is TemporalAssignment.DIRECT_RECORD
    assert resolution.exact_timestamp == datetime(1969, 7, 20, 23, 30, tzinfo=UTC)
    assert resolution.timezone_name == "Europe/Berlin"
    assert resolution.utc_offset_minutes == 60
    assert resolution.timezone_dataset_version == "mini-test"
    assert resolution.interpretation == "derived under Europe/Berlin rules"


def test_persisted_occurrence_of_a_day_precision_claim_is_reported() -> None:
    """DB-free. A claim with no derived block is the reported stated day."""
    from types import SimpleNamespace

    claim = SimpleNamespace(
        assertion_json={
            "value": {
                "time": "+1964-03-27T00:00:00Z",
                "precision": 11,
                "calendarmodel": GREGORIAN,
            }
        }
    )
    resolution = _persisted_occurrence(cast(Claim, claim))
    assert resolution.profile_date == date(1964, 3, 27)
    assert resolution.temporal_precision is TemporalPrecision.DAY
    assert resolution.temporal_assignment is TemporalAssignment.REPORTED
    assert resolution.exact_timestamp is None
    assert resolution.timezone_name is None


@pytest.mark.integration
def test_resolution_reuses_the_ingest_derivation_after_a_boundary_reseed(
    session: Session, tmp_path: Path
) -> None:
    """A reseed between ingest and resolution must not change the derived day.

    Ingest records the derivation (zone + day + dataset version) on the claim,
    which the human reviews. If resolution re-ran the boundary lookup against a
    reseeded table, the EventTime could get a different zone/day than the
    reviewed candidate and the immutable claim snapshot -- self-contradictory
    evidence. Resolution reuses the persisted derivation instead.
    """
    seed_test_timezones(session)
    payload = _entity_document(
        entity_id="Q108subday",
        revision_id=700007,
        timestamp="1969-07-20T23:30:00Z",
        precision=14,
    )
    _ingest(session, payload, 700007, tmp_path)
    _accept_core(session)

    # Reseed so the same coordinates would now resolve to a different zone AND a
    # different local day (New York, UTC-4, puts 23:30Z on the 20th, not the
    # Berlin 21st) under a new dataset version.
    reseed = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"tzid": "America/New_York"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[10, 50], [11, 50], [11, 51], [10, 51], [10, 50]]],
                },
            }
        ],
    }
    load_timezone_boundaries(
        session, geojson_text=json.dumps(reseed), dataset_version="reseed-v2"
    )

    event = resolve_wikidata_event(session)
    event_time = session.scalars(
        select(EventTime).where(
            EventTime.event_id == event.id, EventTime.is_primary.is_(True)
        )
    ).one()
    # The reviewed (ingest) derivation stands: Berlin, the 21st -- not New York /
    # the 20th the reseeded table would now produce.
    assert event_time.start_date == date(1969, 7, 21)
    assert event_time.timezone_name == "Europe/Berlin"

    occurrence_claim = session.scalars(
        select(Claim).where(Claim.claim_type == "candidate_occurrence_date")
    ).one()
    derived = (occurrence_claim.assertion_json or {})["derived_local_date"]
    assert derived["timezone"] == "Europe/Berlin"
    assert derived["timezone_dataset_version"] == "mini-test"


@pytest.mark.integration
def test_publish_refuses_a_derived_candidate_before_its_coordinates_are_accepted(
    session: Session, tmp_path: Path
) -> None:
    """The publish pre-resolution fallback must not derive a day from unaccepted
    place evidence -- doing so would open a merge-review task asserting an
    occurrence on a day derived from unreviewed coordinates, unactionable because
    the event cannot resolve. Publish refuses instead.
    """
    from app.services import LocalFilesystemPublishedProfileStore
    from app.wikidata import publish_wikidata_event

    seed_test_timezones(session)
    payload = _entity_document(
        entity_id="Q108subday",
        revision_id=700008,
        timestamp="1969-07-20T23:30:00Z",
        precision=14,
    )
    _ingest(session, payload, 700008, tmp_path)
    # Accept the core claims but NOT the coordinates (place evidence unreviewed).
    for claim_type in (
        "candidate_event_identity",
        "candidate_event_type",
        "candidate_name",
        "candidate_occurrence_date",
    ):
        record_claim_review(
            session,
            claim=session.scalars(
                select(Claim).where(Claim.claim_type == claim_type)
            ).one(),
            decision=ReviewDecisionValue.ACCEPTED,
            rationale="Core reviewed; coordinates deliberately left pending.",
            reviewed_by="test-human",
        )

    with pytest.raises(ValueError, match="coordinate candidate must be human-accepted"):
        publish_wikidata_event(
            session,
            store=LocalFilesystemPublishedProfileStore(tmp_path / "published"),
        )
