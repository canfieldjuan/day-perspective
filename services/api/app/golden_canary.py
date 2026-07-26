"""Golden-100 canary publication and profile validation (epic #32, AA4).

The canary publishes the deliberately-chosen stress dates before the archive
run publishes everything, and checks each generated profile against the
properties a reader would notice if they broke. The checks are deliberately
about meaning rather than shape: a profile can be schema-valid, hash-clean
and still tell a reader that 1952 had 365 days or that a projection is an
observation.

What it does not do: assert that a human has read the page. A canary-published
date is recorded as ``context_published``, which never satisfies the golden
set's release gate (see ``app.golden_set``).
"""

from __future__ import annotations

import calendar
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.batch_publication import CONTEXT_BATCH_KIND, start_batch_run
from app.coverage import SECTION_KEYS
from app.golden_set import (
    CONTEXT_PUBLISHED_STATUS,
    PUBLISHED_AND_VALIDATED_STATUS,
    validate_golden_set,
)
from app.models import (
    DayProfile,
    PublicationBatchRun,
    PublicationManifest,
    PublicationTier,
)
from app.services import PublishedProfileStore
from app.un_wpp import SUPPORTED_YEARS

GOLDEN_CANARY_KIND = f"{CONTEXT_BATCH_KIND}:golden-canary"

DAILY_EQUIVALENT_ASSIGNMENT = "uniform_period_allocation"
#: Sections a context profile always fills. Declared available and empty,
#: they tell a reader there is nothing when the pipeline simply produced
#: nothing.
ANNUAL_CONTEXT_SECTIONS = (
    "typical_day_in_this_year",
    "wider_historical_context",
)
MODELED_STATUS = "modeled"
_DENOMINATOR = re.compile(r"divided by (\d+) days")


@dataclass(frozen=True)
class CanaryPlan:
    """Golden dates split by whether a pipeline can publish them today."""

    publishable: list[date]
    unsupported: list[date]

    @property
    def total(self) -> int:
        return len(self.publishable) + len(self.unsupported)


def plan_golden_canary(path: Path) -> CanaryPlan:
    """Split the golden set into dates the canary publishes and dates it cannot.

    1900-1949 has no annual-context pipeline. Those dates stay honestly
    unpublished; reporting them as canary failures would train us to ignore
    a failure list that is mostly noise.
    """
    validate_golden_set(path)
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    publishable: list[date] = []
    unsupported: list[date] = []
    for record in payload["records"]:
        profile_date = date.fromisoformat(str(record["date"]))
        if profile_date.year in SUPPORTED_YEARS:
            publishable.append(profile_date)
        else:
            unsupported.append(profile_date)
    return CanaryPlan(
        publishable=sorted(publishable), unsupported=sorted(unsupported)
    )


def current_un_wpp_release_id(session: Session) -> UUID | None:
    """The release a context publication would use right now.

    Recorded on the run so a resume can tell that the ground truth moved
    underneath it.
    """
    from app.models import Source, SourceRelease
    from app.un_wpp import UN_WPP_SOURCE_SLUG

    source = session.scalar(select(Source).where(Source.slug == UN_WPP_SOURCE_SLUG))
    if source is None:
        return None
    return session.scalar(
        select(SourceRelease.id)
        .where(SourceRelease.source_id == source.id)
        .order_by(SourceRelease.ingested_at.desc())
        .limit(1)
    )


def start_golden_canary_run(
    session: Session, *, dates: Sequence[date], force_new_version: bool = False
) -> PublicationBatchRun:
    """Open a ledgered run whose plan is an explicit date list.

    Its own kind keeps the canary out of ``--resume`` for archive-year runs:
    resuming the wrong plan would publish a different selection than asked.
    The run records how it was requested, so a resume finishes the run the
    operator started rather than a differently-flagged one.
    """
    return start_batch_run(
        session,
        kind=GOLDEN_CANARY_KIND,
        requested={
            "selection": "golden-set",
            "dates": [value.isoformat() for value in dates],
            "force_new_version": force_new_version,
            # Each publication independently picks the newest release, so a
            # release ingested mid-run would leave the finished dates on the
            # old one and the resumed dates on the new one — a canary whose
            # verdict covers neither release completely.
            "source_release_id": str(release)
            if (release := current_un_wpp_release_id(session)) is not None
            else None,
        },
    )


def _issue(profile_date: str, message: str) -> str:
    return f"{profile_date}: {message}"


def validate_context_payload(payload: dict[str, Any]) -> list[str]:
    """Check one served profile for meaning defects. Empty list means clean."""
    issues: list[str] = []
    profile_date = str(payload.get("date", "unknown"))
    try:
        year = date.fromisoformat(profile_date).year
    except ValueError:
        return [_issue(profile_date, "profile date is not a calendar date")]

    sections = payload.get("sections")
    if not isinstance(sections, dict):
        return [_issue(profile_date, "profile has no sections map")]
    states = payload.get("section_states")
    if not isinstance(states, dict):
        return [_issue(profile_date, "profile has no section_states map")]

    for key in sorted(set(SECTION_KEYS) | set(sections) | set(states)):
        if key not in SECTION_KEYS:
            issues.append(
                _issue(profile_date, f"{key} is not a contract section key")
            )
            continue
        if key not in sections:
            # A section absent from the payload is not the same as an empty
            # one: the reader is told nothing rather than told there is
            # nothing.
            issues.append(_issue(profile_date, f"{key} is missing from sections"))
            continue
        statements = sections[key]
        state = states.get(key)
        if not isinstance(state, dict):
            issues.append(
                _issue(profile_date, f"{key} has no declared section state")
            )
            continue
        status = state.get("status")
        populated = bool(statements)
        if status == "not_yet_supported":
            if populated:
                issues.append(
                    _issue(
                        profile_date,
                        f"{key} is declared not_yet_supported but carries content",
                    )
                )
            reason = state.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                issues.append(
                    _issue(
                        profile_date,
                        f"{key} is not_yet_supported without a usable reason",
                    )
                )
        elif status != "available":
            issues.append(
                _issue(profile_date, f"{key} has an unknown state {status!r}")
            )

    # A publisher regression that emits no statements while leaving the
    # sections available would otherwise validate clean: every per-statement
    # check iterates nothing.
    for key in ANNUAL_CONTEXT_SECTIONS:
        state = states.get(key)
        available = isinstance(state, dict) and state.get("status") == "available"
        if available and not sections.get(key):
            issues.append(
                _issue(
                    profile_date,
                    f"{key} is available but carries no statements",
                )
            )

    tier = payload.get("publication_tier")
    has_recorded = bool(sections.get("recorded_on_this_date"))
    if tier == PublicationTier.CONTEXT_ONLY.value and has_recorded:
        issues.append(
            _issue(
                profile_date,
                "context_only profile carries a recorded-event statement",
            )
        )
    if tier == PublicationTier.REVIEWED_ENRICHED.value and not has_recorded:
        # The other direction: a tier that promises a recorded event and
        # shows none oversells the page.
        issues.append(
            _issue(
                profile_date,
                "reviewed_enriched profile carries no recorded-event statement",
            )
        )

    for statement in _statements(sections):
        issues.extend(_validate_data_status(statement, profile_date=profile_date))

    expected_days = 366 if calendar.isleap(year) else 365
    marked, unmarked = _daily_equivalents(sections)
    for statement in marked:
        issues.extend(
            _validate_daily_equivalent(
                statement,
                profile_date=profile_date,
                expected_days=expected_days,
                year=year,
            )
        )
    for statement in unmarked:
        issues.append(
            _issue(
                profile_date,
                f"{statement.get('statement_id', '?')} reads as a daily "
                "equivalent but carries no temporal_assignment marker",
            )
        )
    return issues


#: How a daily-equivalent statement reads. The marker is what the UI
#: classifies on, so a statement that reads like one without carrying it is
#: a defect rather than something to skip.
DAILY_EQUIVALENT_TEXT = "average daily"


def _daily_equivalents(
    sections: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Marked daily equivalents, and statements that read like one but are
    not marked."""
    marked: list[dict[str, Any]] = []
    unmarked: list[dict[str, Any]] = []
    for statements in sections.values():
        if not isinstance(statements, list):
            continue
        for statement in statements:
            if not isinstance(statement, dict):
                continue
            details = statement.get("details")
            assignment = (
                details.get("temporal_assignment")
                if isinstance(details, dict)
                else None
            )
            if assignment == DAILY_EQUIVALENT_ASSIGNMENT:
                marked.append(statement)
            elif DAILY_EQUIVALENT_TEXT in str(statement.get("statement", "")).lower():
                unmarked.append(statement)
    return marked, unmarked


def _validate_daily_equivalent(
    statement: dict[str, Any], *, profile_date: str, expected_days: int, year: int
) -> list[str]:
    issues: list[str] = []
    statement_id = str(statement.get("statement_id", "?"))
    details = statement.get("details")
    details = details if isinstance(details, dict) else {}
    text = str(statement.get("statement", ""))
    note = str(statement.get("provenance_note", ""))

    if details.get("days_in_year") != expected_days:
        issues.append(
            _issue(
                profile_date,
                f"{statement_id} divides by {details.get('days_in_year')!r} days "
                f"where the year has {expected_days}",
            )
        )
    prose = _DENOMINATOR.search(note)
    if prose is None:
        issues.append(
            _issue(
                profile_date,
                f"{statement_id} provenance note does not name its denominator",
            )
        )
    elif int(prose.group(1)) != expected_days:
        # The number can be right while the sentence beneath it is wrong, and
        # the sentence is the part a reader believes.
        issues.append(
            _issue(
                profile_date,
                f"{statement_id} provenance note says {prose.group(1)} days "
                f"where the year has {expected_days}",
            )
        )
    if "not an observation" not in text.lower():
        issues.append(
            _issue(
                profile_date,
                f"{statement_id} states a daily equivalent without disclaiming "
                "that it is not an observation",
            )
        )
    displayed = _displayed_count(text)
    expected_value = details.get("average_daily_equivalent")
    if isinstance(expected_value, int):
        if displayed is None:
            issues.append(
                _issue(
                    profile_date,
                    f"{statement_id} states no readable daily count",
                )
            )
        elif displayed != expected_value:
            # The UI renders `statement` directly. A correct derived value
            # under a wrong sentence is a wrong page.
            issues.append(
                _issue(
                    profile_date,
                    f"{statement_id} displays {displayed:,} where its derived "
                    f"value is {expected_value:,}",
                )
            )
    if str(year) not in text:
        # A statement naming a different year than the profile it appears in
        # attributes one year's figures to another.
        issues.append(
            _issue(
                profile_date,
                f"{statement_id} does not name the profile's year {year}",
            )
        )
    return issues


#: How a profile says a number is modeled rather than observed. Context
#: statements say "projects"; daily equivalents say "projection".
PROJECTION_WORDS = ("projection", "projects", "projected")


def _statements(sections: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        statement
        for statements in sections.values()
        if isinstance(statements, list)
        for statement in statements
        if isinstance(statement, dict)
    ]


_DISPLAYED_COUNT = re.compile(r"about ([\d,]+)")


def _displayed_count(text: str) -> int | None:
    """The count a reader actually sees, or None if the sentence has none."""
    match = _DISPLAYED_COUNT.search(text)
    if match is None:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _validate_data_status(
    statement: dict[str, Any], *, profile_date: str
) -> list[str]:
    """A modeled number must say so, and an estimate must not claim to be one.

    Applied to every statement carrying a data status, not only daily
    equivalents: the generated profile puts data_status on its wider-context
    statements too, and a modeled 2025 population that regresses to saying
    "estimates" is exactly the defect this canary exists to catch.
    """
    details = statement.get("details")
    if not isinstance(details, dict) or "data_status" not in details:
        return []
    statement_id = str(statement.get("statement_id", "?"))
    text = str(statement.get("statement", "")).lower()
    projected = details.get("data_status") == MODELED_STATUS
    claims_projection = any(word in text for word in PROJECTION_WORDS)
    if projected and not claims_projection:
        return [
            _issue(
                profile_date,
                f"{statement_id} rests on a projection without saying so",
            )
        ]
    if not projected and claims_projection:
        return [
            _issue(
                profile_date,
                f"{statement_id} presents a {details.get('data_status')} value "
                "as a projection",
            )
        ]
    return []


@dataclass
class CanaryValidation:
    """Validation outcome across a set of published dates."""

    checked: int = 0
    missing: list[date] = field(default_factory=list)
    issues: dict[str, list[str]] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not self.issues and not self.missing

    @staticmethod
    def read_payload(
        session: Session,
        *,
        store: PublishedProfileStore,
        profile_date: date,
    ) -> dict[str, Any]:
        """Read what a reader is served: the artifact, hash-checked."""
        manifest = session.scalar(
            select(PublicationManifest)
            .join(DayProfile, DayProfile.publication_manifest_id == PublicationManifest.id)
            .where(PublicationManifest.profile_date == profile_date)
            .order_by(PublicationManifest.version.desc())
            .limit(1)
        )
        if manifest is None:
            raise LookupError(f"No published profile for {profile_date.isoformat()}.")
        payload = store.read(manifest.storage_uri, manifest.content_hash)
        # The endpoint treats the manifest as the authority for the tier, so
        # an artifact published before the tier existed still reaches readers
        # tiered. Validating the raw artifact would skip the tier checks on
        # exactly those profiles.
        return {**payload, "publication_tier": manifest.publication_tier.value}

    @classmethod
    def of(
        cls,
        session: Session,
        *,
        store: PublishedProfileStore,
        dates: Sequence[date],
    ) -> CanaryValidation:
        result = cls()
        for profile_date in dates:
            try:
                payload = cls.read_payload(
                    session, store=store, profile_date=profile_date
                )
            except LookupError:
                result.missing.append(profile_date)
                continue
            result.checked += 1
            issues = validate_context_payload(payload)
            if issues:
                result.issues[profile_date.isoformat()] = issues
        return result


def record_canary_publication(path: Path, *, dates: Sequence[date]) -> int:
    """Mark canary-published dates in the golden set; return rows changed.

    Never downgrades a human-validated date: the canary knows the machinery
    ran, not that the review was undone.
    """
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    wanted = {value.isoformat() for value in dates}
    changed = 0
    for record in payload["records"]:
        if record["date"] not in wanted:
            continue
        if record.get("publication_status") == PUBLISHED_AND_VALIDATED_STATUS:
            continue
        if record.get("publication_status") == CONTEXT_PUBLISHED_STATUS:
            continue
        record["publication_status"] = CONTEXT_PUBLISHED_STATUS
        changed += 1
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    validate_golden_set(path)
    return changed
