from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.models import ProfileType, profile_type_for_date

REQUIRED_TAGS = {
    "major_conflict",
    "pandemic",
    "large_disaster",
    "scientific_milestone",
    "cultural_milestone",
    "notable_birth_or_death",
    "apocalypse_prediction",
    "sparse_date",
    "timezone_boundary",
    "source_disagreement",
    "leap_calendar",
    "era_boundary",
}
REVIEWED_STATUS = "reviewed"
PENDING_REVIEW_STATUS = "pending_human_review"
REVIEW_STATUSES = {PENDING_REVIEW_STATUS, REVIEWED_STATUS}

NOT_GENERATED_STATUS = "not_generated"
CONTEXT_PUBLISHED_STATUS = "context_published"
PUBLISHED_AND_VALIDATED_STATUS = "published_and_validated"
#: A generated context profile is evidence that the machinery works, not that
#: a human read the page. Only PUBLISHED_AND_VALIDATED_STATUS counts toward
#: release readiness; CONTEXT_PUBLISHED_STATUS is deliberately weaker so the
#: AA5 mass run cannot tick the release gate on its way past.
PUBLICATION_STATUSES = {
    NOT_GENERATED_STATUS,
    CONTEXT_PUBLISHED_STATUS,
    PUBLISHED_AND_VALIDATED_STATUS,
}


@dataclass(frozen=True)
class GoldenSetReport:
    record_count: int
    profile_counts: dict[str, int]
    tag_counts: dict[str, int]
    reviewed_count: int
    published_count: int
    context_published_count: int
    release_ready: bool


def validate_golden_set(path: Path) -> GoldenSetReport:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "1":
        raise ValueError("Golden-set file must use schema version 1.")
    records = payload.get("records")
    if not isinstance(records, list) or len(records) != 100:
        raise ValueError("Golden set must contain exactly 100 date records.")
    dates: set[date] = set()
    profile_counts = {profile.value: 0 for profile in ProfileType}
    tag_counts = {tag: 0 for tag in REQUIRED_TAGS}
    reviewed_count = 0
    published_count = 0
    context_published_count = 0
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("Golden-set records must be objects.")
        try:
            profile_date = date.fromisoformat(str(item["date"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Golden-set dates must be valid ISO dates.") from error
        if profile_date in dates:
            raise ValueError("Golden-set dates must be unique.")
        dates.add(profile_date)
        expected_profile = profile_type_for_date(profile_date)
        if expected_profile is None or item.get("profile_type") != expected_profile.value:
            raise ValueError("Golden-set profile type does not match its date band.")
        profile_counts[expected_profile.value] += 1
        tags = item.get("selection_tags")
        if (
            not isinstance(tags, list)
            or not tags
            or any(tag not in REQUIRED_TAGS for tag in tags)
        ):
            raise ValueError("Golden-set records require supported selection tags.")
        for tag in set(tags):
            tag_counts[str(tag)] += 1
        rationale = item.get("selection_rationale")
        if not isinstance(rationale, str) or len(rationale.strip()) < 40:
            raise ValueError("Golden-set records require a substantive rationale.")
        review_status = item.get("manual_review_status")
        if review_status not in REVIEW_STATUSES:
            # An unrecognised value used to read as "not reviewed", so a typo
            # silently understated the set instead of failing it.
            raise ValueError("Golden-set records require a known review status.")
        if review_status == REVIEWED_STATUS:
            reviewed_count += 1
        publication_status = item.get("publication_status")
        if publication_status not in PUBLICATION_STATUSES:
            raise ValueError(
                "Golden-set records require a known publication status."
            )
        if publication_status == PUBLISHED_AND_VALIDATED_STATUS:
            published_count += 1
        elif publication_status == CONTEXT_PUBLISHED_STATUS:
            context_published_count += 1
    if any(count == 0 for count in profile_counts.values()):
        raise ValueError("Golden set must contain dates from every profile era.")
    if any(count == 0 for count in tag_counts.values()):
        raise ValueError("Golden set does not cover every required stress category.")
    return GoldenSetReport(
        record_count=len(records),
        profile_counts=profile_counts,
        tag_counts=tag_counts,
        reviewed_count=reviewed_count,
        published_count=published_count,
        context_published_count=context_published_count,
        release_ready=reviewed_count == 100 and published_count == 100,
    )
