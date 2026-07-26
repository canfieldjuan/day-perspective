from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import LegalReviewStatus, Source, SourceRelease
from app.services import create_source_release


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
