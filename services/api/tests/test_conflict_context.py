"""Annual conflict context inside ordinary context profiles (epic #51, UC2).

Until now the UCDP statement existed on 1964-03-27 alone, reached through the
golden path. Every other published date said nothing about armed conflict —
not because the evidence was missing, but because nothing carried it there.

These tests pin the honesty properties of carrying it everywhere: the
statement describes its year rather than the date, a year the dataset does
not cover says nothing at all rather than reporting calm, and a human who
declined the content is not overruled by a standing rule.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import LocalFilesystemRawSourceStore
from app.governance import EditorialSelection, EditorialSelectionStatus, record_editorial_selection
from app.models import PublicationManifest, PublicationTier
from app.services import LocalFilesystemPublishedProfileStore
from app.ucdp import ingest_ucdp_annual, review_ucdp_annual
from app.un_wpp import (
    STANDING_ANNUAL_CONTEXT_RULE,
    ingest_un_wpp,
    publish_context_profile,
    review_un_wpp,
)

from .helpers import synthetic_ucdp_multiyear_csv

WPP_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "fixtures"
    / "un-wpp"
    / "wpp2024-world-1950-2025.csv"
)

# The synthetic release covers 1971 and 1972 and deliberately omits 1973, so
# one test date has conflict context and another provably cannot.
COVERED = date(1971, 6, 15)
UNCOVERED = date(1973, 6, 15)


def _conflict_statements(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        statement
        for statement in payload["sections"]["wider_historical_context"]
        if "conflict" in str(statement.get("statement", "")).lower()
    ]


@pytest.fixture()
def reviewed_sources(session: Session, tmp_path: Path) -> None:
    """UN WPP for every supported year, UCDP for 1971 and 1972 only."""
    raw = LocalFilesystemRawSourceStore(tmp_path / "raw")

    wpp = ingest_un_wpp(session, fixture_path=WPP_FIXTURE, raw_store=raw)
    assert wpp.source_release_id is not None
    review_un_wpp(session, wpp.source_release_id)

    fixture = tmp_path / "synthetic-multiyear.csv"
    fixture.write_text(
        synthetic_ucdp_multiyear_csv(
            [("900", "1971"), ("901", "1971"), ("900", "1972")]
        ),
        encoding="utf-8",
    )
    ucdp = ingest_ucdp_annual(session, fixture_path=fixture, raw_store=raw)
    assert ucdp.source_release_id is not None
    review_ucdp_annual(session, ucdp.source_release_id, year=1971)
    review_ucdp_annual(session, ucdp.source_release_id, year=1972)
    session.commit()


def _publish(
    session: Session, tmp_path: Path, profile_date: date
) -> dict[str, Any]:
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile = publish_context_profile(
        session, store=store, profile_date=profile_date
    )
    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert manifest is not None
    return store.read(manifest.storage_uri, manifest.content_hash)


@pytest.mark.integration
def test_a_context_profile_carries_its_years_conflict_statement(
    session: Session, tmp_path: Path, reviewed_sources: None
) -> None:
    payload = _publish(session, tmp_path, COVERED)

    conflicts = _conflict_statements(payload)
    assert len(conflicts) == 1
    statement = conflicts[0]
    # Two synthetic conflicts are active in 1971.
    assert "2 state-based" in str(statement["statement"])
    assert "1971" in str(statement["statement"])
    # The date is June 15; the claim is about the year, and must say so.
    assert "not a count for any single date" in str(statement["statement"])
    assert statement["details"]["temporal_assignment"] == "period_context"


@pytest.mark.integration
def test_a_year_without_conflict_data_says_nothing_about_conflicts(
    session: Session, tmp_path: Path, reviewed_sources: None
) -> None:
    """Absence is absence. A year the dataset does not cover must not be
    published as a year of no conflicts, and must not block the date."""
    payload = _publish(session, tmp_path, UNCOVERED)

    assert payload["date"] == UNCOVERED.isoformat()
    assert _conflict_statements(payload) == []
    # The rest of the profile is unaffected: the date still publishes its
    # demographic context.
    assert len(payload["sections"]["typical_day_in_this_year"]) == 2
    assert payload["section_states"]["wider_historical_context"] == {
        "status": "available"
    }


@pytest.mark.integration
def test_conflict_context_does_not_change_the_publication_tier(
    session: Session, tmp_path: Path, reviewed_sources: None
) -> None:
    """Annual context about a year is still annual context. A conflict count
    is not a recorded event on the date and must not read as one."""
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile = publish_context_profile(session, store=store, profile_date=COVERED)

    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert manifest is not None
    assert manifest.publication_tier is PublicationTier.CONTEXT_ONLY
    payload = store.read(manifest.storage_uri, manifest.content_hash)
    assert payload["publication_tier"] == "context_only"
    assert payload["sections"]["recorded_on_this_date"] == []


@pytest.mark.integration
def test_the_evidence_path_points_at_the_statement_it_describes(
    session: Session, tmp_path: Path, reviewed_sources: None
) -> None:
    """The conflict statement's index is computed from the assembled section,
    not assumed. A fixed index is correct only while the demographic source
    emits exactly the expected number of statements, and silently mislabels
    provenance the moment it does not."""
    from app.models import PublicationStatementEvidence

    payload = _publish(session, tmp_path, COVERED)
    context = payload["sections"]["wider_historical_context"]
    conflict_index = next(
        index
        for index, statement in enumerate(context)
        if "conflict" in str(statement.get("statement", "")).lower()
    )

    manifest = session.scalar(
        select(PublicationManifest).where(
            PublicationManifest.profile_date == COVERED,
            PublicationManifest.status == "published",
        )
    )
    assert manifest is not None
    paths = set(
        session.scalars(
            select(PublicationStatementEvidence.statement_path).where(
                PublicationStatementEvidence.publication_manifest_id == manifest.id
            )
        )
    )
    assert (
        f"/sections/wider_historical_context/{conflict_index}" in paths
    ), f"conflict statement sits at index {conflict_index}; evidence paths are {sorted(paths)}"


@pytest.mark.integration
def test_the_standing_rule_records_the_selection_for_the_published_date(
    session: Session, tmp_path: Path, reviewed_sources: None
) -> None:
    """Publication of 1971-06-15 must not rest on a selection filed against
    1971-01-01. The audit record names the date whose profile it justifies."""
    _publish(session, tmp_path, COVERED)

    selections = list(
        session.scalars(
            select(EditorialSelection).where(
                EditorialSelection.profile_date == COVERED,
                EditorialSelection.section_key == "wider_historical_context",
            )
        )
    )
    conflict_selections = [
        selection
        for selection in selections
        if selection.derived_value_id is not None
    ]
    assert conflict_selections, "the conflict root has no selection for this date"
    assert all(
        selection.reviewed_by == STANDING_ANNUAL_CONTEXT_RULE
        for selection in conflict_selections
    )
    assert all(
        selection.status == EditorialSelectionStatus.SELECTED.value
        for selection in conflict_selections
    )


@pytest.mark.integration
def test_a_prior_human_rejection_is_respected_rather_than_overruled(
    session: Session, tmp_path: Path, reviewed_sources: None
) -> None:
    """A standing rule may fill a gap; it may never reverse a human. Where a
    reviewer declined the conflict statement for a date, the date publishes
    without it — the decision stands and the archive stays publishable."""
    from app.models import DerivedValue

    derived = session.scalar(
        select(DerivedValue).where(
            DerivedValue.value_kind == "active_state_based_conflict_count",
            DerivedValue.period_start == date(1971, 1, 1),
        )
    )
    assert derived is not None
    record_editorial_selection(
        session,
        profile_date=COVERED,
        section_key="wider_historical_context",
        derived_value_id=derived.id,
        status=EditorialSelectionStatus.REJECTED,
        display_rank=10,
        rationale="Reviewer declined this context for this date.",
        reviewed_by="human-reviewer",
    )
    session.commit()

    payload = _publish(session, tmp_path, COVERED)

    assert _conflict_statements(payload) == []
    # The human decision is intact, not superseded by the standing rule.
    latest = session.scalars(
        select(EditorialSelection)
        .where(
            EditorialSelection.profile_date == COVERED,
            EditorialSelection.derived_value_id == derived.id,
        )
        .order_by(EditorialSelection.decision_version.desc())
    ).first()
    assert latest is not None
    assert latest.status == EditorialSelectionStatus.REJECTED.value
    assert latest.reviewed_by == "human-reviewer"


@pytest.mark.integration
def test_unreviewed_conflict_claims_fail_closed_rather_than_publishing_quietly(
    session: Session, tmp_path: Path
) -> None:
    """The other side of the absence rule.

    A year with no records says nothing, which is honest. A year whose
    records exist but have not been reviewed is a different situation
    entirely: evidence is present and would be dropped. Publishing the
    quieter profile would hide it, so the date fails instead.
    """
    raw = LocalFilesystemRawSourceStore(tmp_path / "raw")
    wpp = ingest_un_wpp(session, fixture_path=WPP_FIXTURE, raw_store=raw)
    assert wpp.source_release_id is not None
    review_un_wpp(session, wpp.source_release_id)

    fixture = tmp_path / "unreviewed.csv"
    fixture.write_text(
        synthetic_ucdp_multiyear_csv([("900", "1971")]), encoding="utf-8"
    )
    # Ingested but deliberately not reviewed.
    ingest_ucdp_annual(session, fixture_path=fixture, raw_store=raw)
    session.commit()

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    # Matched rather than bare, so the test cannot pass on some unrelated
    # ValueError from the demographic side of the profile.
    with pytest.raises(ValueError, match="requires accepted claims for 1971"):
        publish_context_profile(session, store=store, profile_date=COVERED)


@pytest.mark.integration
def test_republishing_a_profile_with_conflict_context_is_a_no_op(
    session: Session, tmp_path: Path, reviewed_sources: None
) -> None:
    """UC3 republishes every supported date. If the merged profile were not
    rerun-safe, that pass would mint a second version of all of them."""
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")

    first = publish_context_profile(session, store=store, profile_date=COVERED)
    second = publish_context_profile(session, store=store, profile_date=COVERED)

    assert first.id == second.id
    assert first.publication_manifest_id == second.publication_manifest_id


@pytest.mark.integration
def test_the_manifest_names_every_release_the_profile_rests_on(
    session: Session, tmp_path: Path, reviewed_sources: None
) -> None:
    """A profile drawing on two sources that names one of them cannot be
    reconstructed from its own provenance."""
    _publish(session, tmp_path, COVERED)

    manifest = session.scalar(
        select(PublicationManifest).where(
            PublicationManifest.profile_date == COVERED,
            PublicationManifest.status == "published",
        )
    )
    assert manifest is not None
    release_ids = manifest.metadata_json.get("source_release_ids")
    assert isinstance(release_ids, list)
    assert len(release_ids) == 2, release_ids
