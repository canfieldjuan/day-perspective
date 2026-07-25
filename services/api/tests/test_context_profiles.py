"""Context profiles for arbitrary supported dates (epic #32, slice AA2).

Until now only 1964-03-27 could be published, because editorial selection
existed for that date alone. The standing annual-context rule makes every
1950-2025 date publishable from evidence that has already been reviewed,
without pretending a human considered each date individually.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.governance import EditorialSelection
from app.models import PublicationManifest, PublicationTier
from app.services import LocalFilesystemPublishedProfileStore
from app.un_wpp import (
    STANDING_ANNUAL_CONTEXT_RULE,
    ingest_un_wpp,
    publish_context_profile,
    review_un_wpp,
)

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "fixtures"
    / "un-wpp"
    / "wpp2024-world-1950-2025.csv"
)


@pytest.fixture()
def reviewed_un_wpp(session: Session, tmp_path: Path) -> None:
    from app.adapters.base import LocalFilesystemRawSourceStore

    result = ingest_un_wpp(
        session,
        fixture_path=FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    assert result.source_release_id is not None
    review_un_wpp(session, result.source_release_id)
    session.commit()


@pytest.mark.integration
def test_a_date_outside_the_golden_profile_can_be_published(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1971, 6, 15)

    profile = publish_context_profile(
        session, store=store, profile_date=profile_date
    )

    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert manifest is not None
    assert manifest.publication_tier is PublicationTier.CONTEXT_ONLY
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    assert payload["date"] == profile_date.isoformat()
    assert payload["publication_tier"] == "context_only"
    assert payload["sections"]["recorded_on_this_date"] == []
    assert len(payload["sections"]["typical_day_in_this_year"]) == 2
    assert len(payload["sections"]["wider_historical_context"]) == 3
    # The year's averages must never be presented as counts for the date.
    for statement in payload["sections"]["typical_day_in_this_year"]:
        assert "1971" in statement["statement"]
        assert "not an observation for" in statement["statement"]


@pytest.mark.integration
def test_the_standing_rule_records_accountable_selections(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(1980, 2, 29)

    publish_context_profile(session, store=store, profile_date=profile_date)

    selections = list(
        session.scalars(
            select(EditorialSelection).where(
                EditorialSelection.profile_date == profile_date
            )
        )
    )
    assert selections, "Publication must leave an editorial record for the date."
    assert {row.reviewed_by for row in selections} == {STANDING_ANNUAL_CONTEXT_RULE}
    assert all(row.decision_version == 1 for row in selections)
    assert all(row.status == "selected" for row in selections)


@pytest.mark.integration
def test_republishing_a_context_profile_is_a_rerun_safe_no_op(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile_date = date(2001, 9, 11)

    first = publish_context_profile(session, store=store, profile_date=profile_date)
    second = publish_context_profile(session, store=store, profile_date=profile_date)

    assert second.id == first.id
    assert list(
        session.scalars(
            select(PublicationManifest.version).where(
                PublicationManifest.profile_date == profile_date
            )
        )
    ) == [1]
    # The standing rule must not append a decision version on every rerun.
    versions = list(
        session.scalars(
            select(EditorialSelection.decision_version).where(
                EditorialSelection.profile_date == profile_date
            )
        )
    )
    assert versions and max(versions) == 1


@pytest.mark.integration
def test_leap_day_uses_the_leap_year_denominator(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    leap = publish_context_profile(
        session, store=store, profile_date=date(1964, 2, 29)
    )
    common = publish_context_profile(
        session, store=store, profile_date=date(1965, 3, 1)
    )

    leap_manifest = session.get(PublicationManifest, leap.publication_manifest_id)
    common_manifest = session.get(PublicationManifest, common.publication_manifest_id)
    assert leap_manifest is not None and common_manifest is not None
    leap_payload = store.read(leap_manifest.storage_uri, leap_manifest.content_hash)
    common_payload = store.read(
        common_manifest.storage_uri, common_manifest.content_hash
    )
    leap_statement = leap_payload["sections"]["typical_day_in_this_year"][0]
    common_statement = common_payload["sections"]["typical_day_in_this_year"][0]
    assert leap_statement["details"]["days_in_year"] == 366
    assert common_statement["details"]["days_in_year"] == 365


@pytest.mark.integration
def test_projection_years_say_so(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """1950-2023 are estimates; 2024-2025 are medium-variant projections and
    the published wording must not blur them (D026)."""
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    projected = publish_context_profile(
        session, store=store, profile_date=date(2025, 5, 1)
    )
    manifest = session.get(PublicationManifest, projected.publication_manifest_id)
    assert manifest is not None
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    context = payload["sections"]["wider_historical_context"]
    assert any(
        statement["details"].get("data_status") == "modeled" for statement in context
    )


@pytest.mark.integration
def test_dates_outside_un_wpp_coverage_fail_closed(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    with pytest.raises(ValueError):
        publish_context_profile(session, store=store, profile_date=date(1949, 12, 31))
