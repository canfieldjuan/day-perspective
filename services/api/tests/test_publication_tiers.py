"""Publication tiers (epic #32, slice AA1).

A date carrying only annual demographic context is useful but is not
equivalent to a date with a reviewed recorded event. The distinction must be
derivable from the published payload alone, stored on the manifest, and
carried in the artifact so the API, index, and interface all agree.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DayProfile, ProfileType, PublicationManifest, PublicationTier
from app.services import (
    LocalFilesystemPublishedProfileStore,
    PublicationStatementEvidenceInput,
    create_claim,
    derive_publication_tier,
    publish_day_profile,
    resolve_claim,
)
from tests.helpers import source_release
from tests.test_publication_atomicity import statement_evidence

PROFILE_DATE = date(1969, 7, 20)
PROFILE_TYPE = ProfileType.STANDARD_STATISTICAL


def profile_payload(sections: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    return {
        "schema_version": "1",
        "date": PROFILE_DATE.isoformat(),
        "profile_type": PROFILE_TYPE.value,
        "sections": sections,
    }


def statement(identifier: str) -> dict[str, object]:
    return {"statement_id": identifier, "statement": "Synthetic tier statement."}


def date_specific_statement(identifier: str) -> dict[str, object]:
    """An editorial statement tied to the selected date.

    MD3 (#62) promotes an editorial section only when it carries a statement
    whose temporal assignment is date-specific. ``direct_record`` is one such
    assignment; a bare :func:`statement` carries none and stays context_only.
    """
    return {
        "statement_id": identifier,
        "statement": "Synthetic tier statement.",
        "details": {"temporal_assignment": "direct_record"},
    }


def test_context_only_covers_period_and_annual_context() -> None:
    assert (
        derive_publication_tier(
            profile_payload(
                {
                    "recorded_on_this_date": [],
                    "typical_day_in_this_year": [statement("avg")],
                    "wider_historical_context": [statement("pop")],
                    "evidence_notes": [statement("quality")],
                }
            )
        )
        is PublicationTier.CONTEXT_ONLY
    )


def test_context_only_when_the_profile_carries_nothing_yet() -> None:
    assert (
        derive_publication_tier(profile_payload({"evidence_notes": []}))
        is PublicationTier.CONTEXT_ONLY
    )


@pytest.mark.parametrize(
    "section",
    ["curated_claims", "derived_comparisons", "wonder_and_progress"],
)
def test_non_date_specific_editorial_material_stays_context_only(
    section: str,
) -> None:
    """MD3 (#62): editorial content promotes only when it is tied to the
    selected date. A statement with no date-specific temporal assignment — an
    annual average, an approved period comparison — is context_only however
    many of them a page carries, otherwise every page becomes enriched because
    another annual statistic was added."""
    assert (
        derive_publication_tier(
            profile_payload(
                {
                    "recorded_on_this_date": [],
                    "typical_day_in_this_year": [statement("avg")],
                    section: [statement("editorial")],
                }
            )
        )
        is PublicationTier.CONTEXT_ONLY
    )


@pytest.mark.parametrize(
    "section",
    ["curated_claims", "derived_comparisons", "wonder_and_progress"],
)
def test_date_specific_editorial_material_is_partially_enriched(
    section: str,
) -> None:
    """The other side of the MD3 guard: a curated claim or modeled value whose
    date applies to this day does promote — that is exactly what
    partially_enriched describes."""
    assert (
        derive_publication_tier(
            profile_payload(
                {
                    "recorded_on_this_date": [],
                    "typical_day_in_this_year": [statement("avg")],
                    section: [date_specific_statement("editorial")],
                }
            )
        )
        is PublicationTier.PARTIALLY_ENRICHED
    )


def test_a_recorded_event_makes_the_profile_enriched() -> None:
    assert (
        derive_publication_tier(
            profile_payload(
                {
                    "recorded_on_this_date": [statement("event")],
                    "typical_day_in_this_year": [statement("avg")],
                }
            )
        )
        is PublicationTier.ENRICHED
    )


def test_tiers_are_ordered_from_sparse_to_rich() -> None:
    assert PublicationTier.CONTEXT_ONLY.rank == 0
    assert PublicationTier.PARTIALLY_ENRICHED.rank == 1
    assert PublicationTier.ENRICHED.rank == 2


def test_malformed_sections_degrade_to_context_only_without_raising() -> None:
    for broken in ({"sections": None}, {"sections": {"recorded_on_this_date": "no"}}, {}):
        assert derive_publication_tier(broken) is PublicationTier.CONTEXT_ONLY


@pytest.mark.integration
def test_publication_records_the_tier_on_the_manifest_and_in_the_artifact(
    session: Session, tmp_path: Path
) -> None:
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)
    payload = {
        "schema_version": "1",
        "date": PROFILE_DATE.isoformat(),
        "profile_type": PROFILE_TYPE.value,
        "sections": {
            "evidence_notes": [
                {"statement_id": "tier-test", "statement": "Context-only profile."}
            ]
        },
    }
    profile = publish_day_profile(
        session,
        store=store,
        profile_date=PROFILE_DATE,
        profile_type=PROFILE_TYPE,
        payload=payload,
        statement_evidence=evidence,
    )

    manifest = session.get(PublicationManifest, profile.publication_manifest_id)
    assert manifest is not None
    assert manifest.publication_tier is PublicationTier.CONTEXT_ONLY

    stored = store.read(manifest.storage_uri, manifest.content_hash)
    assert stored["publication_tier"] == "context_only"
    assert "publication_tier" not in payload, (
        "Publication must not mutate the caller's payload."
    )


@pytest.mark.integration
def test_tier_is_queryable_for_coverage(session: Session, tmp_path: Path) -> None:
    """The index built in AA3 filters on this column, so it must be stored,
    not merely embedded in the artifact."""
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    # Every published statement needs its own provenance mapping, so an
    # enriched profile carries evidence for the recorded event too. Both
    # mappings come from one release: the shared helper creates a source with
    # a fixed slug and cannot be called twice in a single test.
    release = source_release(session)
    note_claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:tier-evidence-note",
        claim_type="synthetic_assertion",
        assertion_text="Synthetic evidence note for tier testing.",
    )
    note_resolved = resolve_claim(
        session,
        canonical_key="test:tier-evidence-note",
        resolved_value={"statement": "Enriched profile."},
        rationale="Test-only evidence note.",
        supporting_claim_ids=[note_claim.id],
    )
    event_claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:tier-recorded-event",
        claim_type="synthetic_assertion",
        assertion_text="Synthetic recorded event for tier testing.",
    )
    event_resolved = resolve_claim(
        session,
        canonical_key="test:tier-recorded-event",
        resolved_value={"statement": "A recorded event."},
        rationale="Test-only recorded event.",
        supporting_claim_ids=[event_claim.id],
    )
    evidence = [
        PublicationStatementEvidenceInput(
            statement_path="/sections/recorded_on_this_date/0",
            resolved_claim_id=event_resolved.id,
        ),
        PublicationStatementEvidenceInput(
            statement_path="/sections/evidence_notes/0",
            resolved_claim_id=note_resolved.id,
        ),
    ]
    publish_day_profile(
        session,
        store=store,
        profile_date=PROFILE_DATE,
        profile_type=PROFILE_TYPE,
        payload={
            "schema_version": "1",
            "date": PROFILE_DATE.isoformat(),
            "profile_type": PROFILE_TYPE.value,
            "sections": {
                "recorded_on_this_date": [
                    {"statement_id": "event", "statement": "A recorded event."}
                ],
                "evidence_notes": [
                    {"statement_id": "tier-test", "statement": "Enriched profile."}
                ],
            },
        },
        statement_evidence=evidence,
    )
    assert list(
        session.scalars(
            select(PublicationManifest.publication_tier).where(
                PublicationManifest.publication_tier
                == PublicationTier.ENRICHED
            )
        )
    ) == [PublicationTier.ENRICHED]


@pytest.mark.integration
def test_api_serves_the_backfilled_tier_for_a_pre_tier_artifact(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Artifacts published before tiers existed are byte-immutable and carry
    none. Their backfilled tier must still reach the API and interface, or
    coverage would report richness the product never shows."""
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from app import main
    from app.database import get_session
    from app.models import PublicationStatus
    from app.services import content_hash

    store = LocalFilesystemPublishedProfileStore(tmp_path)
    legacy_date = date(1971, 6, 15)
    legacy_payload: dict[str, object] = {
        "schema_version": "1",
        "date": legacy_date.isoformat(),
        "profile_type": PROFILE_TYPE.value,
        "sections": {"evidence_notes": []},
    }
    assert "publication_tier" not in legacy_payload
    staged = store.stage_versioned(legacy_date, 1, legacy_payload)
    staged.finalize()
    session.add(
        PublicationManifest(
            profile_date=legacy_date,
            profile_type=PROFILE_TYPE,
            version=1,
            editorial_revision=1,
            status=PublicationStatus.PUBLISHED,
            published_at=datetime.now(UTC),
            publication_tier=PublicationTier.PARTIALLY_ENRICHED,
            content_hash=content_hash(legacy_payload),
            source_snapshot_hash="0" * 64,
            storage_uri=staged.storage_uri,
            code_version="test",
            metadata_json={},
        )
    )
    session.flush()
    legacy_manifest = session.scalar(
        select(PublicationManifest).where(
            PublicationManifest.profile_date == legacy_date
        )
    )
    assert legacy_manifest is not None
    session.add(
        DayProfile(
            profile_date=legacy_date,
            profile_type=PROFILE_TYPE,
            publication_manifest_id=legacy_manifest.id,
            content_hash=legacy_manifest.content_hash,
        )
    )
    session.commit()

    monkeypatch.setattr(main.settings, "published_profile_root", tmp_path)

    def override_session() -> object:
        yield session

    main.app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(main.app).get(f"/api/v1/day/{legacy_date.isoformat()}")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["publication_tier"] == "partially_enriched"
    # The artifact itself is untouched: verification still runs on its bytes.
    assert "publication_tier" not in store.read(
        staged.storage_uri, content_hash(legacy_payload)
    )
