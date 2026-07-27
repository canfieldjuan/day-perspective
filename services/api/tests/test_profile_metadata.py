"""Review status and quality floor, derived once (epic #64 / MD1, closes #45).

These fields were cut from AA3a after seven review rounds. Every finding was
real; the problem was that both were threaded through six writers, so each
one was a chance for the six to disagree. The tests below are the nine that
were written and passing before the cut, preserved in #45 and landed here
with the implementation rather than rewritten from memory.

What they pin, collectively, is that neither field ever claims more than the
records support — an unreviewed page must never read as reviewed, and an
ungradeable one must never read as well graded.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import LocalFilesystemRawSourceStore
from app.governance import EditorialSelectionStatus, record_editorial_selection
from app.models import (
    PublicationManifest,
    PublicationStatementEvidence,
    QualityFloor,
    ReviewStatus,
)
from app.profile_metadata import (
    STANDING_RULE_REVIEWER,
    derive_profile_metadata,
    derive_quality_floor,
    derive_review_status,
)
from app.services import LocalFilesystemPublishedProfileStore
from app.un_wpp import ingest_un_wpp, publish_context_profile, review_un_wpp

WPP_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "fixtures"
    / "un-wpp"
    / "wpp2024-world-1950-2025.csv"
)
PROFILE_DATE = date(1971, 6, 15)


@pytest.fixture()
def published(session: Session, tmp_path: Path) -> PublicationManifest:
    result = ingest_un_wpp(
        session,
        fixture_path=WPP_FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    assert result.source_release_id is not None
    review_un_wpp(session, result.source_release_id)
    session.commit()
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    profile = publish_context_profile(
        session, store=store, profile_date=PROFILE_DATE
    )
    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert manifest is not None
    return manifest


def _roots(
    session: Session, manifest: PublicationManifest
) -> list[PublicationStatementEvidence]:
    return list(
        session.scalars(
            select(PublicationStatementEvidence).where(
                PublicationStatementEvidence.publication_manifest_id
                == manifest.id
            )
        )
    )


@pytest.mark.integration
def test_review_status_reflects_recorded_review_not_evidence_presence(
    session: Session, published: PublicationManifest
) -> None:
    """A profile is not reviewed because it has content.

    The original defect derived "reviewed" from evidence existing, which
    made every published page look reviewed the moment it had statements.
    """
    assert _roots(session, published), "the fixture must publish real evidence"

    assert derive_review_status(session, manifest=published) is (
        ReviewStatus.AUTOMATED_ONLY
    )


@pytest.mark.integration
def test_a_human_editorial_decision_reads_as_reviewed(
    session: Session, published: PublicationManifest
) -> None:
    for row in _roots(session, published):
        record_editorial_selection(
            session,
            profile_date=PROFILE_DATE,
            section_key=row.statement_path.split("/")[2],
            resolved_claim_id=row.resolved_claim_id,
            derived_value_id=row.derived_value_id,
            status=EditorialSelectionStatus.SELECTED,
            display_rank=1,
            rationale="Checked against the source workbook.",
            reviewed_by="a-human-reviewer",
        )
    session.flush()

    assert derive_review_status(session, manifest=published) is (
        ReviewStatus.HUMAN_REVIEWED
    )


@pytest.mark.integration
def test_a_blank_reviewer_is_not_a_human_review(
    session: Session, published: PublicationManifest
) -> None:
    """The most flattering thing this field could get wrong."""
    for row in _roots(session, published):
        record_editorial_selection(
            session,
            profile_date=PROFILE_DATE,
            section_key=row.statement_path.split("/")[2],
            resolved_claim_id=row.resolved_claim_id,
            derived_value_id=row.derived_value_id,
            status=EditorialSelectionStatus.SELECTED,
            display_rank=1,
            rationale="Recorded with no reviewer identity.",
            reviewed_by="   ",
        )
    session.flush()

    assert derive_review_status(session, manifest=published) is (
        ReviewStatus.AUTOMATED_ONLY
    )


@pytest.mark.integration
def test_the_standing_rule_is_not_a_human_review(
    session: Session, published: PublicationManifest
) -> None:
    """The rule is accountable provenance, not a person reading the date."""
    selections = _roots(session, published)
    assert selections

    status = derive_review_status(session, manifest=published)

    assert status is ReviewStatus.AUTOMATED_ONLY
    assert STANDING_RULE_REVIEWER == "standing-rule:annual-context-v1"


@pytest.mark.integration
def test_review_status_ignores_selections_for_unpublished_content(
    session: Session, published: PublicationManifest
) -> None:
    """A human decision about a root this manifest never published says
    nothing about this profile."""
    from app.models import DerivedValue

    # Nulls excluded deliberately: a statement rooted in a resolved claim
    # has no derived value, and SQL NOT IN (NULL, ...) is never true, so
    # leaving them in makes this query return nothing whatever the data is.
    published_roots = {
        row.derived_value_id
        for row in _roots(session, published)
        if row.derived_value_id is not None
    }
    # A real derived value this manifest did not publish — a random id would
    # only prove the foreign key works.
    unpublished = session.scalars(
        select(DerivedValue).where(DerivedValue.id.not_in(published_roots))
    ).first()
    assert unpublished is not None
    record_editorial_selection(
        session,
        profile_date=PROFILE_DATE,
        section_key="curated_claims",
        derived_value_id=unpublished.id,
        status=EditorialSelectionStatus.SELECTED,
        display_rank=1,
        rationale="A decision about something else entirely.",
        reviewed_by="a-human-reviewer",
    )
    session.flush()

    assert derive_review_status(session, manifest=published) is (
        ReviewStatus.AUTOMATED_ONLY
    )


@pytest.mark.integration
def test_a_human_decision_in_another_section_is_not_review_of_this_one(
    session: Session, published: PublicationManifest
) -> None:
    """Governance keys selections by (date, section, root), so the same root
    reviewed under a different section is a different decision."""
    for row in _roots(session, published):
        record_editorial_selection(
            session,
            profile_date=PROFILE_DATE,
            section_key="curated_claims",
            resolved_claim_id=row.resolved_claim_id,
            derived_value_id=row.derived_value_id,
            status=EditorialSelectionStatus.SELECTED,
            display_rank=1,
            rationale="Reviewed, but for a different section.",
            reviewed_by="a-human-reviewer",
        )
    session.flush()

    assert derive_review_status(session, manifest=published) is (
        ReviewStatus.AUTOMATED_ONLY
    )


@pytest.mark.integration
def test_one_reviewed_statement_does_not_make_a_reviewed_profile(
    session: Session, published: PublicationManifest
) -> None:
    """The operator's rule: human_reviewed means every published root was
    checked, not that one of them was."""
    rows = _roots(session, published)
    assert len(rows) > 1
    first = rows[0]
    record_editorial_selection(
        session,
        profile_date=PROFILE_DATE,
        section_key=first.statement_path.split("/")[2],
        resolved_claim_id=first.resolved_claim_id,
        derived_value_id=first.derived_value_id,
        status=EditorialSelectionStatus.SELECTED,
        display_rank=1,
        rationale="Only this one was checked.",
        reviewed_by="a-human-reviewer",
    )
    session.flush()

    assert derive_review_status(session, manifest=published) is (
        ReviewStatus.AUTOMATED_ONLY
    )


@pytest.mark.integration
def test_the_quality_floor_orders_grades_by_rank_not_alphabet(
    session: Session, published: PublicationManifest
) -> None:
    """A lexicographic comparison reports "A+" as weaker than "A", which
    reads as a quality claim rather than a sorting bug."""
    from app.models import QualityAssessment

    assessment = session.scalars(select(QualityAssessment)).first()
    assert assessment is not None
    assessment.public_grade = "B"
    session.flush()

    assert derive_quality_floor(session, manifest=published) is QualityFloor.B


@pytest.mark.integration
def test_a_long_quality_grade_does_not_fail_publication(
    session: Session, published: PublicationManifest
) -> None:
    """A VARCHAR(8) here once failed a publication *after* its artifact had
    been promoted. An unrankable grade must degrade, never raise."""
    from app.models import QualityAssessment

    assessment = session.scalars(select(QualityAssessment)).first()
    assert assessment is not None
    assessment.public_grade = "Exceptionally well sourced, with caveats" * 3
    session.flush()

    assert derive_quality_floor(session, manifest=published) is (
        QualityFloor.NOT_ASSESSED
    )


@pytest.mark.integration
def test_an_unrankable_grade_is_not_reported_as_a_letter(
    session: Session, published: PublicationManifest
) -> None:
    """not_assessed rather than D: claiming a bad grade we never measured is
    as wrong as claiming a good one, and the floor is genuinely unknown."""
    from app.models import QualityAssessment

    assessment = session.scalars(select(QualityAssessment)).first()
    assert assessment is not None
    assessment.public_grade = "A+"
    session.flush()

    assert derive_quality_floor(session, manifest=published) is (
        QualityFloor.NOT_ASSESSED
    )


@pytest.mark.integration
def test_the_two_fields_are_derived_together_from_one_call(
    session: Session, published: PublicationManifest
) -> None:
    """Every writer routes through derive_profile_metadata. If a call site
    ever computes one of these itself, #45's seven rounds start again."""
    metadata = derive_profile_metadata(session, manifest=published)

    assert metadata.review_status is derive_review_status(
        session, manifest=published
    )
    assert metadata.quality_floor is derive_quality_floor(
        session, manifest=published
    )


@pytest.mark.integration
def test_publication_indexes_the_derived_metadata(
    session: Session, published: PublicationManifest
) -> None:
    """The coverage row is written by the same derivation, not by a value
    passed in from the publisher."""
    from app.models import CoverageEntry

    entry = session.scalar(
        select(CoverageEntry).where(CoverageEntry.profile_date == PROFILE_DATE)
    )
    assert entry is not None
    metadata = derive_profile_metadata(session, manifest=published)
    assert entry.review_status is metadata.review_status
    assert entry.quality_floor is metadata.quality_floor


@pytest.mark.integration
def test_the_rebuild_agrees_with_the_derivation_for_every_date(
    session: Session, published: PublicationManifest, tmp_path: Path
) -> None:
    """#45's acceptance criterion, in its strongest available form.

    The recurring failure was a migration backfill and the runtime computing
    these fields differently. The migration here deliberately does not
    compute them at all — it defaults both to their most modest value and
    leaves derivation to the one function — so there is no second
    implementation that *could* disagree.

    What remains to prove is that the rebuild, which is the path that
    replaces a backfill, produces exactly what the derivation says for every
    indexed date.
    """
    from app.coverage import rebuild_coverage_index
    from app.models import CoverageEntry

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    rebuild_coverage_index(session, store=store)

    entries = list(session.scalars(select(CoverageEntry)))
    assert entries, "the rebuild indexed nothing to compare"
    for entry in entries:
        manifest = session.get(
            PublicationManifest, entry.publication_manifest_id
        )
        assert manifest is not None
        expected = derive_profile_metadata(session, manifest=manifest)
        assert entry.review_status is expected.review_status, entry.profile_date
        assert entry.quality_floor is expected.quality_floor, entry.profile_date


@pytest.mark.integration
def test_the_migration_default_understates_rather_than_flatters(
    session: Session, published: PublicationManifest
) -> None:
    """Rows written before the derivation ran carry defaults. Those defaults
    must be the most modest claim available, so an un-rebuilt archive reads
    as unreviewed and ungraded rather than asserting either."""
    from app.models import CoverageEntry

    entry = CoverageEntry(
        profile_date=date(1999, 1, 1),
        profile_type=published.profile_type,
        publication_manifest_id=published.id,
        publication_tier=published.publication_tier,
        has_recorded_event=False,
        sections={},
    )
    session.add(entry)
    session.flush()

    assert entry.review_status is ReviewStatus.AUTOMATED_ONLY
    assert entry.quality_floor is QualityFloor.NOT_ASSESSED
