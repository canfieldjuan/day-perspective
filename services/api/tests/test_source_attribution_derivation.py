"""What "every source supporting this profile" is allowed to mean (G3b-2b).

The page-level attribution list is derived from the evidence a profile
publishes. That makes two categories easy to include by accident, because both
genuinely appear inside a published evidence snapshot:

- the publisher of a claim the resolution **rejected**, which is recorded with
  ``stance: "dissenting"`` precisely so a reader can see it was considered and
  not accepted, and
- the publisher of a release an included release was **derived from**, which is
  ancestry rather than a party standing behind the page.

Crediting either says something the profile does not mean. A dissenting
publisher is named as supporting the very claim it disputed, which inverts the
record; an ancestor is credited for a page that never read it.

Both stay visible in per-statement provenance, which is authoritative. This is
only the summary, and a summary that quietly widens its subject is a false one.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import (
    LegalReviewStatus,
    Source,
    SourceLineage,
    SourceLineageRelationship,
    SourceRelease,
)
from app.services import (
    PublicationStatementEvidenceInput,
    create_claim,
    create_source_release,
    resolve_claim,
    sources_supporting_evidence,
)
from tests.helpers import source_release


def _other_source(session: Session, *, slug: str, name: str) -> Source:
    source = Source(
        slug=slug,
        name=name,
        publisher=f"{name} publisher",
        canonical_url=f"https://example.invalid/{slug}",
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.add(source)
    session.flush()
    return source


def _release_for(session: Session, source: Source, label: str) -> SourceRelease:
    return create_source_release(
        session,
        source_id=source.id,
        release_label=label,
        source_url=f"https://example.invalid/{source.slug}/{label}",
        raw_storage_uri=f"test://raw/{source.slug}-{label}",
        raw_bytes=f"{source.slug} {label} bytes".encode(),
        raw_record_count=1,
    )


def test_a_dissenting_publisher_is_not_credited_as_supporting(
    session: Session,
) -> None:
    """The record says this source was rejected; the summary must not say kept."""
    accepted = source_release(session)
    disputing_source = _other_source(
        session, slug="disputing-source", name="Disputing archive"
    )
    disputing = _release_for(session, disputing_source, "v1")

    supporting = create_claim(
        session,
        source_release_id=accepted.id,
        source_record_locator="record:accepted",
        claim_type="synthetic_assertion",
        assertion_text="The accepted assertion.",
    )
    rejected = create_claim(
        session,
        source_release_id=disputing.id,
        source_record_locator="record:rejected",
        claim_type="synthetic_assertion",
        assertion_text="The rejected assertion.",
    )
    resolved = resolve_claim(
        session,
        canonical_key="test:attribution-dissent",
        resolved_value={"statement": "The resolved value."},
        rationale="Attribution test resolution.",
        supporting_claim_ids=[supporting.id],
        dissenting_claim_ids=[rejected.id],
    )

    attributions = sources_supporting_evidence(
        session,
        [
            PublicationStatementEvidenceInput(
                statement_path="/sections/evidence_notes/0",
                resolved_claim_id=resolved.id,
            )
        ],
    )

    names = {entry["name"] for entry in attributions}
    assert "Synthetic source for tests" in names
    assert "Disputing archive" not in names, (
        "a publisher whose claim the resolution rejected is credited as "
        "supporting the profile"
    )


def test_a_lineage_ancestor_is_not_credited(session: Session) -> None:
    """Ancestry is a chain, not a party standing behind the page."""
    ancestor_source = _other_source(
        session, slug="ancestor-source", name="Ancestor archive"
    )
    ancestor = _release_for(session, ancestor_source, "v1")

    published_source = _other_source(
        session, slug="published-source", name="Published archive"
    )
    published = _release_for(session, published_source, "v1")

    session.add(
        SourceLineage(
            child_release_id=published.id,
            parent_release_id=ancestor.id,
            relationship=SourceLineageRelationship.DERIVED,
            note="Test-only derived lineage.",
        )
    )
    session.flush()

    claim = create_claim(
        session,
        source_release_id=published.id,
        source_record_locator="record:derived",
        claim_type="synthetic_assertion",
        assertion_text="An assertion from a derived release.",
    )
    resolved = resolve_claim(
        session,
        canonical_key="test:attribution-lineage",
        resolved_value={"statement": "The resolved value."},
        rationale="Attribution lineage test.",
        supporting_claim_ids=[claim.id],
    )

    attributions = sources_supporting_evidence(
        session,
        [
            PublicationStatementEvidenceInput(
                statement_path="/sections/evidence_notes/0",
                resolved_claim_id=resolved.id,
            )
        ],
    )

    names = {entry["name"] for entry in attributions}
    assert "Published archive" in names
    assert "Ancestor archive" not in names, (
        "a release the published one was derived from is credited as a source "
        "standing behind the page"
    )


def test_an_unknown_publisher_or_url_is_omitted_not_emitted_empty(
    session: Session,
) -> None:
    """Absence is expressed by omitting the key, not by the empty string.

    `Source.publisher` and `Source.canonical_url` are both nullable. Coercing
    a missing one to "" makes "unknown" and "the empty string" the same
    payload, which is unavailable data encoded as a present value.
    """
    unattributed_source = Source(
        slug="no-publisher-source",
        name="An archive with no recorded publisher or URL",
        publisher=None,
        canonical_url=None,
        legal_review_status=LegalReviewStatus.NOT_REQUIRED,
    )
    session.add(unattributed_source)
    session.flush()
    release = _release_for(session, unattributed_source, "v1")

    claim = create_claim(
        session,
        source_release_id=release.id,
        source_record_locator="record:unattributed",
        claim_type="synthetic_assertion",
        assertion_text="An assertion from an unattributed source.",
    )
    resolved = resolve_claim(
        session,
        canonical_key="test:attribution-unknown-publisher",
        resolved_value={"statement": "The resolved value."},
        rationale="Attribution omission test.",
        supporting_claim_ids=[claim.id],
    )

    attributions = sources_supporting_evidence(
        session,
        [
            PublicationStatementEvidenceInput(
                statement_path="/sections/evidence_notes/0",
                resolved_claim_id=resolved.id,
            )
        ],
    )

    entry = next(e for e in attributions if e["name"] == unattributed_source.name)
    assert "publisher" not in entry, (
        "an unknown publisher must be omitted, not encoded as an empty string"
    )
    assert "url" not in entry, (
        "an unknown URL must be omitted, not encoded as an empty string"
    )
