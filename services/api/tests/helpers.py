from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.models import LegalReviewStatus, Source, SourceRelease
from app.services import create_source_release
from app.timezone_boundaries import (
    resolve_timezone_from_coordinates,
    seed_timezone_boundaries,
)
from app.usgs import USGSEarthquakeAdapter

_MINI_TIMEZONES = Path(__file__).parent / "fixtures" / "mini_timezones.geojson"


def seed_test_timezones(session: Session) -> None:
    """Seed the mini timezone-boundary fixture (covers the USGS golden epicenter).

    The real ~170MB release is a pinned download the suite never fetches; this
    small committed fixture gives ``ST_Covers`` the America/Anchorage polygon a
    USGS ingest needs to derive its day from coordinates (A3b).
    """
    seed_timezone_boundaries(
        session,
        url="",
        sha256="",
        dataset_version="mini-test",
        fixture_path=_MINI_TIMEZONES,
    )


def usgs_test_adapter(session: Session) -> USGSEarthquakeAdapter:
    """A USGS adapter whose day derivation reads this session's tz boundary table."""
    return USGSEarthquakeAdapter(
        resolve_timezone=resolve_timezone_from_coordinates(session)
    )


def source_release(session: Session) -> SourceRelease:
    source = Source(
        slug="test-source",
        name="Synthetic source for tests",
        publisher="Test suite",
        canonical_url="https://example.invalid/test-source",
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.add(source)
    session.flush()
    return create_source_release(
        session,
        source_id=source.id,
        release_label="test-v1",
        source_url="https://example.invalid/test-source/v1",
        raw_storage_uri="test://raw/test-v1",
        raw_bytes=b"test raw source bytes",
        raw_record_count=1,
    )


def synthetic_ucdp_multiyear_csv(
    rows: list[tuple[str, str]], version: str = "26.1"
) -> str:
    """A deliberately synthetic multi-year UCDP annual release.

    SYNTHETIC — not UCDP data. It exercises the multi-year invariants, which
    the committed 1964 excerpt cannot because it covers one year. The excerpt
    stays the provenance canary; this never leaves the test suite and must
    never be published.
    """
    header = (
        "conflict_id,location,side_a,side_b,year,intensity_level,"
        "type_of_conflict,start_date,start_prec,region,version"
    )
    lines = [header]
    for conflict_id, year in rows:
        lines.append(
            f"{conflict_id},SyntheticLand,Government of SyntheticLand,"
            f"Synthetic Opposition,{year},1,3,1948-12-31,3,3,{version}"
        )
    return "\n".join(lines) + "\n"
