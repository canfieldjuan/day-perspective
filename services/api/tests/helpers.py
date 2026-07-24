from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import LegalReviewStatus, Source
from app.services import create_source_release


def source_release(session: Session):
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

