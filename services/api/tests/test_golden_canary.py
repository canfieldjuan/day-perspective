"""Golden-100 canary publication (epic #32, slice AA4).

Before publishing 27,759 dates, publish the 100 deliberately-chosen stress
dates and check every generated profile against properties a reader would
notice if they broke: leap-year denominators, projection-versus-estimate
wording, honest unsupported sections, and an intact evidence chain.

The canary is the last cheap place to find these. After AA5 the same defect
is 27,759 wrong pages.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.batch_publication import batch_run_is_resumable, outstanding_dates, run_context_batch
from app.golden_canary import (
    GOLDEN_CANARY_KIND,
    CanaryValidation,
    plan_golden_canary,
    record_canary_publication,
    start_golden_canary_run,
    validate_context_payload,
)
from app.golden_set import (
    CONTEXT_PUBLISHED_STATUS,
    NOT_GENERATED_STATUS,
    PUBLISHED_AND_VALIDATED_STATUS,
    validate_golden_set,
)
from app.services import LocalFilesystemPublishedProfileStore
from app.un_wpp import ingest_un_wpp, review_un_wpp

GOLDEN_SET = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "golden-set"
    / "golden-dates-v1.json"
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


def _daily_statement(
    *,
    year: int = 1952,
    days_in_year: int = 366,
    prose_days: int = 366,
    data_status: str = "estimated",
    projection_wording: bool = False,
) -> dict[str, object]:
    qualifier = (
        ", based on the UN WPP medium-variant projection" if projection_wording else ""
    )
    return {
        "statement_id": "average-daily-births",
        "statement": (
            f"Average daily births in {year}{qualifier}: about 1,000. "
            "This is an average daily equivalent based on the annual total, "
            "not an observation for February 29."
        ),
        "details": {
            "days_in_year": days_in_year,
            "temporal_assignment": "uniform_period_allocation",
            "data_status": data_status,
        },
        "provenance_note": (
            f"UN WPP annual value divided by {prose_days} days; not date-specific."
        ),
    }


def _mapping(payload: dict[str, object], key: str) -> dict[str, Any]:
    """A mutable copy of one payload sub-map, typed once here rather than
    cast at every call site."""
    value = payload[key]
    assert isinstance(value, dict)
    return dict(value)


def _support(payload: dict[str, object], key: str) -> None:
    """Declare a section available, for tests that put content in it."""
    states = _mapping(payload, "section_states")
    states[key] = {"status": "available"}
    payload["section_states"] = states


def _payload(
    *,
    profile_date: str = "1952-02-29",
    typical: list[dict[str, object]] | None = None,
    context: list[dict[str, object]] | None = None,
    section_states: dict[str, object] | None = None,
) -> dict[str, object]:
    sections: dict[str, list[dict[str, object]]] = {
        "recorded_on_this_date": [],
        "typical_day_in_this_year": (
            [_daily_statement()] if typical is None else typical
        ),
        "wider_historical_context": list(context or []),
        "curated_claims": [],
        "derived_comparisons": [],
        "wonder_and_progress": [],
        "evidence_notes": [],
    }
    supported = {"recorded_on_this_date", "typical_day_in_this_year"}
    if context:
        supported.add("wider_historical_context")
    states: dict[str, object] = section_states or {
        key: (
            {"status": "available"}
            if key in supported
            else {
                "status": "not_yet_supported",
                "reason": "This vertical slice does not publish this evidence class.",
            }
        )
        for key in sections
    }
    return {
        "schema_version": "1",
        "date": profile_date,
        "profile_type": "standard_statistical",
        "publication_tier": "context_only",
        "sections": sections,
        "section_states": states,
    }


class TestPayloadValidation:
    def test_a_correct_leap_year_profile_passes(self) -> None:
        assert validate_context_payload(_payload()) == []

    def test_a_wrong_leap_year_denominator_is_caught(self) -> None:
        payload = _payload(
            typical=[_daily_statement(days_in_year=365, prose_days=365)]
        )

        issues = validate_context_payload(payload)

        assert any("366" in issue for issue in issues), issues

    def test_prose_and_numeric_denominators_must_agree(self) -> None:
        # The value can be right while the sentence under it is wrong; a
        # reader only ever sees the sentence.
        payload = _payload(
            typical=[_daily_statement(days_in_year=366, prose_days=365)]
        )

        issues = validate_context_payload(payload)

        assert any("provenance note" in issue.lower() for issue in issues), issues

    def test_a_non_leap_year_denominator_is_accepted(self) -> None:
        payload = _payload(
            profile_date="1953-06-15",
            typical=[
                _daily_statement(year=1953, days_in_year=365, prose_days=365)
            ],
        )

        assert validate_context_payload(payload) == []

    def test_a_projected_year_must_say_it_is_a_projection(self) -> None:
        payload = _payload(
            profile_date="2025-06-15",
            typical=[
                _daily_statement(
                    year=2025,
                    days_in_year=365,
                    prose_days=365,
                    data_status="modeled",
                    projection_wording=False,
                )
            ],
        )

        issues = validate_context_payload(payload)

        assert any("projection" in issue.lower() for issue in issues), issues

    def test_a_projected_year_that_says_so_passes(self) -> None:
        payload = _payload(
            profile_date="2025-06-15",
            typical=[
                _daily_statement(
                    year=2025,
                    days_in_year=365,
                    prose_days=365,
                    data_status="modeled",
                    projection_wording=True,
                )
            ],
        )

        assert validate_context_payload(payload) == []

    def test_an_estimate_must_not_be_dressed_as_a_projection(self) -> None:
        payload = _payload(
            typical=[
                _daily_statement(data_status="estimated", projection_wording=True)
            ]
        )

        issues = validate_context_payload(payload)

        assert any("projection" in issue.lower() for issue in issues), issues

    def test_a_daily_equivalent_must_disclaim_being_an_observation(self) -> None:
        statement = _daily_statement()
        statement["statement"] = "Average daily births in 1952: about 1,000."

        issues = validate_context_payload(_payload(typical=[statement]))

        assert any("observation" in issue.lower() for issue in issues), issues

    def test_an_unsupported_section_must_carry_a_reason(self) -> None:
        payload = _payload()
        states = _mapping(payload, "section_states")
        states["curated_claims"] = {"status": "not_yet_supported"}
        payload["section_states"] = states

        issues = validate_context_payload(payload)

        assert any("curated_claims" in issue for issue in issues), issues

    def test_a_section_with_content_may_not_be_declared_unsupported(self) -> None:
        payload = _payload()
        states = _mapping(payload, "section_states")
        states["typical_day_in_this_year"] = {
            "status": "not_yet_supported",
            "reason": "nope",
        }
        payload["section_states"] = states

        issues = validate_context_payload(payload)

        assert any("typical_day_in_this_year" in issue for issue in issues), issues

    def test_every_section_needs_a_declared_state(self) -> None:
        payload = _payload()
        states = _mapping(payload, "section_states")
        del states["wonder_and_progress"]
        payload["section_states"] = states

        issues = validate_context_payload(payload)

        assert any("wonder_and_progress" in issue for issue in issues), issues

    def test_a_context_profile_may_not_claim_a_recorded_event(self) -> None:
        payload = _payload()
        sections = _mapping(payload, "sections")
        sections["recorded_on_this_date"] = [
            {"statement_id": "x", "statement": "An earthquake struck."}
        ]
        payload["sections"] = sections

        issues = validate_context_payload(payload)

        assert any("context_only" in issue for issue in issues), issues


    def test_a_section_missing_from_the_payload_is_caught(self) -> None:
        # Absent is not the same as empty: the reader is told nothing
        # rather than told there is nothing.
        payload = _payload()
        sections = _mapping(payload, "sections")
        del sections["curated_claims"]
        payload["sections"] = sections

        issues = validate_context_payload(payload)

        assert any("curated_claims is missing" in issue for issue in issues), issues

    def test_a_section_key_outside_the_contract_is_caught(self) -> None:
        payload = _payload()
        sections = _mapping(payload, "sections")
        sections["recordedXonYthisZdate"] = []
        payload["sections"] = sections

        issues = validate_context_payload(payload)

        assert any("not a contract section key" in issue for issue in issues), issues

    def test_a_daily_equivalent_that_lost_its_marker_is_caught(self) -> None:
        # The guard must not pass merely because the thing it guards
        # disappeared: the marker is what the UI classifies on.
        statement = _daily_statement()
        statement["details"] = {"data_status": "estimated"}

        issues = validate_context_payload(_payload(typical=[statement]))

        assert any("no temporal_assignment marker" in issue for issue in issues), issues

    def test_a_statement_naming_another_year_is_caught(self) -> None:
        payload = _payload(
            profile_date="1953-06-15",
            typical=[
                _daily_statement(year=1952, days_in_year=365, prose_days=365)
            ],
        )

        issues = validate_context_payload(payload)

        assert any("does not name the profile's year" in issue for issue in issues), issues

    def test_an_enriched_tier_without_a_recorded_event_is_caught(self) -> None:
        # The other direction of the tier check: a tier that promises a
        # recorded event and shows none oversells the page.
        payload = _payload()
        payload["publication_tier"] = "enriched"

        issues = validate_context_payload(payload)

        assert any("carries no recorded-event" in issue for issue in issues), issues


    def test_a_modeled_context_statement_must_say_it_is_projected(self) -> None:
        # Context statements carry data_status too, and say "projects"
        # rather than "projection" — a check scoped to daily equivalents
        # would never see this regression.
        payload = _payload()
        sections = _mapping(payload, "sections")
        sections["wider_historical_context"] = [
            {
                "statement_id": "population",
                "statement": "UN WPP estimates the mid-2025 world population "
                "at about 8.232 billion.",
                "details": {"data_status": "modeled"},
            }
        ]
        payload["sections"] = sections
        _support(payload, "wider_historical_context")

        issues = validate_context_payload(payload)

        assert any("without saying so" in issue for issue in issues), issues

    def test_a_modeled_context_statement_that_says_projects_passes(self) -> None:
        payload = _payload()
        sections = _mapping(payload, "sections")
        sections["wider_historical_context"] = [
            {
                "statement_id": "population",
                "statement": "UN WPP projects the mid-2025 world population "
                "at about 8.232 billion.",
                "details": {"data_status": "modeled"},
            }
        ]
        payload["sections"] = sections
        _support(payload, "wider_historical_context")

        assert validate_context_payload(payload) == []

    def test_an_estimated_context_statement_may_not_claim_projection(self) -> None:
        payload = _payload()
        sections = _mapping(payload, "sections")
        sections["wider_historical_context"] = [
            {
                "statement_id": "population",
                "statement": "UN WPP projects the mid-1952 world population "
                "at about 2.6 billion.",
                "details": {"data_status": "estimated"},
            }
        ]
        payload["sections"] = sections
        _support(payload, "wider_historical_context")

        issues = validate_context_payload(payload)

        assert any("as a projection" in issue for issue in issues), issues

    def test_a_non_string_unsupported_reason_is_rejected(self) -> None:
        # str() would make 123 look like a perfectly good reason, while the
        # web contract rejects the payload and shows an error instead.
        payload = _payload()
        states = _mapping(payload, "section_states")
        states["curated_claims"] = {"status": "not_yet_supported", "reason": 123}
        payload["section_states"] = states

        issues = validate_context_payload(payload)

        assert any("without a usable reason" in issue for issue in issues), issues


    def test_a_wrong_displayed_count_is_caught(self) -> None:
        # The UI renders `statement` directly, so a correct derived value
        # under a wrong sentence is still a wrong page.
        statement = _daily_statement()
        statement["details"] = {
            **_mapping(statement, "details"),
            "average_daily_equivalent": 999,
        }

        issues = validate_context_payload(_payload(typical=[statement]))

        assert any("displays 1,000 where" in issue for issue in issues), issues

    def test_a_matching_displayed_count_passes(self) -> None:
        statement = _daily_statement()
        statement["details"] = {
            **_mapping(statement, "details"),
            "average_daily_equivalent": 1000,
        }

        assert validate_context_payload(_payload(typical=[statement])) == []

    def test_an_available_annual_section_may_not_be_empty(self) -> None:
        # A publisher regression that emits nothing would otherwise pass
        # every per-statement check by iterating nothing.
        payload = _payload(typical=[])

        issues = validate_context_payload(payload)

        assert any(
            "typical_day_in_this_year is available but carries no statements" in issue
            for issue in issues
        ), issues


class TestGoldenSetStatuses:
    def test_the_canary_plan_only_offers_dates_the_pipeline_supports(self) -> None:
        plan = plan_golden_canary(GOLDEN_SET)

        assert plan.publishable, "the golden set must contain supported dates"
        assert all(value.year >= 1950 for value in plan.publishable)
        assert all(value.year < 1950 for value in plan.unsupported)
        assert len(plan.publishable) + len(plan.unsupported) == 100
        # 1900-1949 has no pipeline: those dates stay honestly unpublished
        # rather than being reported as canary failures.
        assert plan.unsupported

    def test_context_published_is_not_release_ready(self, tmp_path: Path) -> None:
        # A generated context profile is not a reviewed one. If the mass
        # publication run could tick the release gate, the gate is decorative.
        path = tmp_path / "golden.json"
        payload = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
        for record in payload["records"]:
            record["publication_status"] = CONTEXT_PUBLISHED_STATUS
            record["manual_review_status"] = "reviewed"
        path.write_text(json.dumps(payload), encoding="utf-8")

        report = validate_golden_set(path)

        assert report.context_published_count == 100
        assert report.published_count == 0
        assert report.release_ready is False

    def test_human_reviewed_and_validated_is_release_ready(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "golden.json"
        payload = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
        for record in payload["records"]:
            record["publication_status"] = PUBLISHED_AND_VALIDATED_STATUS
            record["manual_review_status"] = "reviewed"
        path.write_text(json.dumps(payload), encoding="utf-8")

        report = validate_golden_set(path)

        assert report.published_count == 100
        assert report.release_ready is True

    def test_an_unknown_publication_status_is_rejected(self, tmp_path: Path) -> None:
        # Previously an unrecognised status silently counted as nothing, so a
        # typo read as "not published" instead of failing the file.
        path = tmp_path / "golden.json"
        payload = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
        payload["records"][0]["publication_status"] = "published-and-validated"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match="publication status"):
            validate_golden_set(path)

    def test_an_unknown_review_status_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "golden.json"
        payload = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
        payload["records"][0]["manual_review_status"] = "looks_fine_to_me"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match="review status"):
            validate_golden_set(path)

    def test_the_shipped_golden_set_is_valid(self) -> None:
        report = validate_golden_set(GOLDEN_SET)

        assert report.record_count == 100
        assert report.release_ready is False

    def test_recording_a_canary_publication_updates_only_that_date(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "golden.json"
        path.write_text(GOLDEN_SET.read_text(encoding="utf-8"), encoding="utf-8")

        record_canary_publication(path, dates=[date(1952, 2, 29)])

        payload = json.loads(path.read_text(encoding="utf-8"))
        statuses = {
            record["date"]: record["publication_status"]
            for record in payload["records"]
        }
        assert statuses["1952-02-29"] == CONTEXT_PUBLISHED_STATUS
        assert statuses["1900-03-15"] == NOT_GENERATED_STATUS
        # The writeback must not disturb human review state.
        assert all(
            record["manual_review_status"] == "pending_human_review"
            for record in payload["records"]
        )
        validate_golden_set(path)

    def test_recording_never_downgrades_a_validated_date(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "golden.json"
        payload = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
        for record in payload["records"]:
            if record["date"] == "1952-02-29":
                record["publication_status"] = PUBLISHED_AND_VALIDATED_STATUS
        path.write_text(json.dumps(payload), encoding="utf-8")

        record_canary_publication(path, dates=[date(1952, 2, 29)])

        after = json.loads(path.read_text(encoding="utf-8"))
        statuses = {
            record["date"]: record["publication_status"] for record in after["records"]
        }
        assert statuses["1952-02-29"] == PUBLISHED_AND_VALIDATED_STATUS


@pytest.mark.integration
class TestCanaryRun:
    def test_the_canary_publishes_and_validates_real_profiles(
        self, session: Session, tmp_path: Path, reviewed_un_wpp: None
    ) -> None:
        store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
        dates = [date(1952, 2, 29), date(1953, 6, 15), date(2025, 12, 31)]
        run = start_golden_canary_run(session, dates=dates)

        report = run_context_batch(
            session, store=store, dates=dates, batch_run=run
        )
        validation = CanaryValidation.of(
            session, store=store, dates=dates
        )

        assert report.published == 3
        assert report.failed == 0
        assert run.kind == GOLDEN_CANARY_KIND
        assert validation.checked == 3
        assert validation.issues == {}

    def test_a_leap_day_profile_divides_by_366(
        self, session: Session, tmp_path: Path, reviewed_un_wpp: None
    ) -> None:
        # 1952-02-29 exists in the golden set precisely to catch a 365 here.
        store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
        dates = [date(1952, 2, 29)]
        run = start_golden_canary_run(session, dates=dates)
        run_context_batch(session, store=store, dates=dates, batch_run=run)

        payload = CanaryValidation.read_payload(
            session, store=store, profile_date=date(1952, 2, 29)
        )
        statements = payload["sections"]["typical_day_in_this_year"]

        assert statements
        for statement in statements:
            assert statement["details"]["days_in_year"] == 366
            assert "366 days" in statement["provenance_note"]

    def test_an_interrupted_canary_resumes_from_its_ledger(
        self, session: Session, tmp_path: Path, reviewed_un_wpp: None
    ) -> None:
        store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
        dates = [date(1952, 2, 29), date(1953, 6, 15), date(2025, 12, 31)]
        run = start_golden_canary_run(session, dates=dates)

        run_context_batch(session, store=store, dates=dates[:1], batch_run=run)

        assert outstanding_dates(session, batch_run=run) == dates[1:]

        resumed = run_context_batch(
            session,
            store=store,
            dates=outstanding_dates(session, batch_run=run),
            batch_run=run,
        )

        assert resumed.published == 2
        assert outstanding_dates(session, batch_run=run) == []


@pytest.mark.integration
def test_a_resumed_canary_finishes_the_run_it_was_asked_for(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """A resume that drops --force-new-version would leave half the canary on
    a new version and half on the old one."""
    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    dates = [date(1952, 2, 29), date(1953, 6, 15)]
    seeded = start_golden_canary_run(session, dates=dates)
    run_context_batch(session, store=store, dates=dates, batch_run=seeded)

    run = start_golden_canary_run(session, dates=dates, force_new_version=True)

    assert run.requested["force_new_version"] is True


@pytest.mark.integration
def test_a_stale_canary_run_does_not_block_recovery(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """An unfinished run recorded against inputs that have since changed must
    be stepped over. Returning it blocks every later resume behind a ledger
    the CLI offers no way to clear."""
    from app.batch_publication import recoverable_batch_run

    store = LocalFilesystemPublishedProfileStore(tmp_path / "published")
    plan = plan_golden_canary(GOLDEN_SET)

    stale_dates = [date(1953, 6, 15), date(1954, 6, 15)]
    stale = start_golden_canary_run(session, dates=stale_dates)
    run_context_batch(
        session, store=store, dates=stale_dates[:1], batch_run=stale
    )
    assert outstanding_dates(session, batch_run=stale) == stale_dates[1:]

    current_dates = plan.publishable[:2]
    current = start_golden_canary_run(session, dates=current_dates)
    run_context_batch(
        session, store=store, dates=current_dates[:1], batch_run=current
    )
    assert outstanding_dates(session, batch_run=current) == current_dates[1:]

    def resumable(candidate: object) -> bool:
        recorded = getattr(candidate, "requested", None) or {}
        return [
            date.fromisoformat(str(value)) for value in recorded.get("dates", [])
        ] in ([], current_dates)

    # Oldest-first alone would return the stale run forever.
    oldest = recoverable_batch_run(session, kind=GOLDEN_CANARY_KIND)
    assert oldest is not None and oldest.id == stale.id

    selected = recoverable_batch_run(
        session, kind=GOLDEN_CANARY_KIND, is_resumable=resumable
    )
    assert selected is not None
    assert selected.id == current.id, "a stale run blocked a resumable one"


def _conflict_statement(
    *,
    count: int = 17,
    year: int = 1952,
    text: str | None = None,
    assignment: str | None = "period_context",
    value: object = None,
) -> dict[str, object]:
    details: dict[str, object] = {
        "title": f"State-based armed conflicts active in {year}",
        "value": count if value is None else value,
        "unit": "conflict-year records",
        "data_status": "final",
    }
    if assignment is not None:
        details["temporal_assignment"] = assignment
    return {
        "statement_id": f"ucdp-{year}-active-conflicts",
        "statement": text
        if text is not None
        else (
            f"UCDP/PRIO records {count} state-based armed conflicts as active "
            f"at some point in {year}. This is annual context, not a count "
            "for any single date in it."
        ),
        "details": details,
    }


class TestConflictContextValidation:
    """The conflict statement appears on every date of its year (UC2), so
    the failures that matter are it describing a different year, reading as
    an event on the date, or claiming mortality it does not measure."""

    def test_a_correct_conflict_statement_passes(self) -> None:
        assert validate_context_payload(
            _payload(context=[_conflict_statement()])
        ) == []

    def test_a_neighbouring_years_count_is_caught(self) -> None:
        # The whole risk of year-general content: 1951's count published on
        # a 1952 page reads as true and is not.
        issues = validate_context_payload(
            _payload(context=[_conflict_statement(year=1951)])
        )
        assert any("reports 1951" in issue for issue in issues), issues

    def test_an_unmarked_conflict_statement_is_caught(self) -> None:
        issues = validate_context_payload(
            _payload(context=[_conflict_statement(assignment=None)])
        )
        assert any("period_context marker" in issue for issue in issues), issues

    def test_prose_and_displayed_count_must_agree(self) -> None:
        issues = validate_context_payload(
            _payload(context=[_conflict_statement(count=17, value=8)])
        )
        assert any("prose says 17" in issue for issue in issues), issues

    def test_a_prefix_count_does_not_pass_as_agreement(self) -> None:
        # "5" is a substring of "53". A containment check would call this
        # agreement; it is a profile understating a conflict count by 48.
        issues = validate_context_payload(
            _payload(context=[_conflict_statement(count=53, value=5)])
        )
        assert any("prose says 53" in issue for issue in issues), issues

    def test_conflict_presence_may_not_be_stated_as_deaths(self) -> None:
        # UCDP/PRIO annual records establish that a conflict was active, not
        # how many it killed; battle-related deaths are a separate dataset
        # covering a shorter span.
        issues = validate_context_payload(
            _payload(
                context=[
                    _conflict_statement(
                        text=(
                            "UCDP/PRIO records 17 state-based armed conflicts "
                            "as active at some point in 1952, with 4,300 "
                            "deaths. This is annual context, not a count for "
                            "any single date in it."
                        )
                    )
                ]
            )
        )
        assert any("mortality terms" in issue for issue in issues), issues

    def test_a_statement_that_omits_the_year_caveat_is_caught(self) -> None:
        issues = validate_context_payload(
            _payload(
                context=[
                    _conflict_statement(
                        text=(
                            "UCDP/PRIO records 17 state-based armed conflicts "
                            "as active at some point in 1952."
                        )
                    )
                ]
            )
        )
        assert any(
            "describes the year rather than this date" in issue for issue in issues
        ), issues


class TestCanaryReleasePinning:
    """A canary verdict must cover one set of inputs completely.

    Since UC2 a context profile rests on two releases, so pinning only the
    demographic one leaves the conflict release free to move mid-run: the
    dates already published would carry release A's counts and the resumed
    ones release B's, and the run would still report a clean verdict.
    """

    DATES = [date(1952, 2, 29), date(1953, 6, 15)]

    def _recorded(self, **overrides: object) -> dict[str, Any]:
        recorded: dict[str, Any] = {
            "dates": [value.isoformat() for value in self.DATES],
            "source_release_id": "11111111-1111-1111-1111-111111111111",
            "ucdp_source_release_id": "22222222-2222-2222-2222-222222222222",
        }
        recorded.update(overrides)
        return recorded

    def _current(self, **overrides: object) -> dict[str, Any]:
        current: dict[str, Any] = {
            "source_release_id": UUID("11111111-1111-1111-1111-111111111111"),
            "ucdp_source_release_id": UUID(
                "22222222-2222-2222-2222-222222222222"
            ),
        }
        current.update(overrides)
        return current

    def test_unchanged_inputs_resume(self) -> None:
        assert batch_run_is_resumable(
            self._recorded(), dates=self.DATES, current_releases=self._current()
        )

    def test_a_moved_conflict_release_blocks_the_resume(self) -> None:
        # The case this pinning exists for: only the UCDP release moved.
        assert not batch_run_is_resumable(
            self._recorded(),
            dates=self.DATES,
            current_releases=self._current(
                ucdp_source_release_id=UUID(
                    "33333333-3333-3333-3333-333333333333"
                )
            ),
        )

    def test_a_moved_demographic_release_still_blocks_the_resume(self) -> None:
        # The pre-existing guard must survive the generalization.
        assert not batch_run_is_resumable(
            self._recorded(),
            dates=self.DATES,
            current_releases=self._current(
                source_release_id=UUID("33333333-3333-3333-3333-333333333333")
            ),
        )

    def test_a_different_golden_set_blocks_the_resume(self) -> None:
        assert not batch_run_is_resumable(
            self._recorded(dates=["1960-01-01"]),
            dates=self.DATES,
            current_releases=self._current(),
        )

    def test_a_ledger_predating_conflict_context_is_not_blocked(self) -> None:
        # Runs recorded before UC2 carry no UCDP key. That is an absent
        # constraint, not a mismatch — treating it as a mismatch would strand
        # every pre-existing interrupted run.
        recorded = self._recorded()
        del recorded["ucdp_source_release_id"]
        assert batch_run_is_resumable(
            recorded, dates=self.DATES, current_releases=self._current()
        )

    def test_a_conflict_source_that_disappeared_blocks_the_resume(self) -> None:
        # The run rested on a conflict release and there is none now, so the
        # resumed dates would lack content the finished ones carry.
        assert not batch_run_is_resumable(
            self._recorded(),
            dates=self.DATES,
            current_releases=self._current(ucdp_source_release_id=None),
        )


@pytest.mark.integration
def test_the_canary_run_pins_both_releases_it_rests_on(
    session: Session, tmp_path: Path, reviewed_un_wpp: None
) -> None:
    """The recording side of the same guard: a predicate that compares a key
    the run never wrote would silently always pass."""
    from app.adapters.base import LocalFilesystemRawSourceStore
    from app.ucdp import ingest_ucdp_annual

    from .helpers import synthetic_ucdp_multiyear_csv

    fixture = tmp_path / "conflicts.csv"
    fixture.write_text(
        synthetic_ucdp_multiyear_csv([("900", "1952")]), encoding="utf-8"
    )
    ingested = ingest_ucdp_annual(
        session,
        fixture_path=fixture,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    session.commit()

    run = start_golden_canary_run(session, dates=[date(1952, 2, 29)])

    recorded = run.requested or {}
    assert recorded.get("source_release_id") is not None
    assert recorded.get("ucdp_source_release_id") == str(
        ingested.source_release_id
    )


class TestConflictCaveatWording:
    """The caveat is a property, not a sentence.

    The archive holds two honest phrasings: the year-general form UC2
    introduced when one statement began serving every date in its year, and
    the date-specific form that predates it and is still the more precise
    of the two on a profile published for one date.
    """

    def test_the_year_general_form_passes(self) -> None:
        assert validate_context_payload(
            _payload(context=[_conflict_statement()])
        ) == []

    def test_the_date_specific_form_passes(self) -> None:
        # This is the archive's only enriched profile, verbatim.
        # A literal check on the year-general sentence failed it.
        assert validate_context_payload(
            _payload(
                profile_date="1964-03-27",
                typical=[
                    _daily_statement(year=1964, days_in_year=366, prose_days=366)
                ],
                context=[
                    _conflict_statement(
                        count=25,
                        year=1964,
                        text=(
                            "UCDP/PRIO records 25 state-based armed conflicts "
                            "as active at some point in 1964. This is annual "
                            "context, not a March 27 count."
                        ),
                    )
                ],
            )
        ) == []

    def test_a_caveat_naming_a_different_day_is_caught(self) -> None:
        """A caveat that disclaims some other day disclaims nothing.

        Widening the check to accept the date-specific form must not widen
        it to accept any date: on a 1964-03-27 profile, "not a March 28
        count" leaves the reader with a count they may still take as
        March 27's.
        """
        issues = validate_context_payload(
            _payload(
                profile_date="1964-03-27",
                typical=[
                    _daily_statement(year=1964, days_in_year=366, prose_days=366)
                ],
                context=[
                    _conflict_statement(
                        count=25,
                        year=1964,
                        text=(
                            "UCDP/PRIO records 25 state-based armed conflicts "
                            "as active at some point in 1964. This is annual "
                            "context, not a March 28 count."
                        ),
                    )
                ],
            )
        )
        assert any(
            "describes the year rather than this date" in issue
            for issue in issues
        ), issues

    def test_a_caveat_naming_a_different_month_is_caught(self) -> None:
        issues = validate_context_payload(
            _payload(
                profile_date="1964-03-27",
                typical=[
                    _daily_statement(year=1964, days_in_year=366, prose_days=366)
                ],
                context=[
                    _conflict_statement(
                        count=25,
                        year=1964,
                        text=(
                            "UCDP/PRIO records 25 state-based armed conflicts "
                            "as active at some point in 1964. This is annual "
                            "context, not an April 27 count."
                        ),
                    )
                ],
            )
        )
        assert any(
            "describes the year rather than this date" in issue
            for issue in issues
        ), issues

    def test_a_capitalised_non_month_is_not_a_caveat(self) -> None:
        # The pattern accepts a capitalised word; only real month names may
        # satisfy it, or "not a Tuesday 27 count" would validate.
        issues = validate_context_payload(
            _payload(
                profile_date="1964-03-27",
                typical=[
                    _daily_statement(year=1964, days_in_year=366, prose_days=366)
                ],
                context=[
                    _conflict_statement(
                        count=25,
                        year=1964,
                        text=(
                            "UCDP/PRIO records 25 state-based armed conflicts "
                            "as active at some point in 1964. This is annual "
                            "context, not a Tuesday 27 count."
                        ),
                    )
                ],
            )
        )
        assert any(
            "describes the year rather than this date" in issue
            for issue in issues
        ), issues

    def test_a_statement_with_no_caveat_at_all_is_still_caught(self) -> None:
        # Widening the accepted forms must not widen it to everything.
        issues = validate_context_payload(
            _payload(
                context=[
                    _conflict_statement(
                        text=(
                            "UCDP/PRIO records 17 state-based armed conflicts "
                            "as active at some point in 1952. Conditions were "
                            "much the same on this date."
                        )
                    )
                ]
            )
        )
        assert any(
            "describes the year rather than this date" in issue
            for issue in issues
        ), issues


def _comparison_statement(
    *,
    text: str | None = None,
    value: object = 74,
    cohort_size: object = 80,
    root_type: str = "derived_value",
    model_card: object = "conflict-count-vs-reference-percentile-v2",
) -> dict[str, object]:
    details: dict[str, object] = {
        "value": value,
        "cohort_size": cohort_size,
        "data_status": "final",
    }
    if model_card is not None:
        details["model_card"] = model_card
    return {
        "statement_id": "conflict-vs-percentile-1952",
        "statement": text
        if text is not None
        else (
            "The selected date occurred during a year with 46 active "
            "state-based conflicts, ranking higher than 74% of 80 supported "
            "years (1946\u20132025). This comparison describes the year, not "
            "this specific day."
        ),
        "details": details,
        "provenance": {"root_type": root_type},
    }


class TestComparisonValidation:
    """The one statement the application asserts on its own account (UC4),
    so the burden is heavier than for a source's claim."""

    def _payload_with(self, statement: dict[str, object]) -> dict[str, object]:
        payload = _payload()
        sections = _mapping(payload, "sections")
        sections["derived_comparisons"] = [statement]
        payload["sections"] = sections
        _support(payload, "derived_comparisons")
        return payload

    def test_a_correct_comparison_passes(self) -> None:
        assert validate_context_payload(
            self._payload_with(_comparison_statement())
        ) == []

    def test_a_comparison_without_a_model_card_is_caught(self) -> None:
        # "No comparison ships without one" is the directory's rule; this is
        # what makes it more than a convention.
        issues = validate_context_payload(
            self._payload_with(_comparison_statement(model_card=None))
        )
        assert any("no model card" in issue for issue in issues), issues

    def test_a_comparison_rooted_in_a_source_claim_is_caught(self) -> None:
        issues = validate_context_payload(
            self._payload_with(_comparison_statement(root_type="resolved_claim"))
        )
        assert any("app-derived but its root is" in issue for issue in issues), issues

    def test_prose_and_displayed_rank_must_agree(self) -> None:
        issues = validate_context_payload(
            self._payload_with(_comparison_statement(value=3))
        )
        assert any("displayed value is 3" in issue for issue in issues), issues

    def test_the_stated_denominator_must_match_the_cohort(self) -> None:
        # A rank without its cohort size is unreadable: "higher than 95%" of
        # four years and of eighty years are very different claims.
        issues = validate_context_payload(
            self._payload_with(_comparison_statement(cohort_size=12))
        )
        assert any("cohort size is 12" in issue for issue in issues), issues

    def test_a_rank_no_year_can_reach_is_caught(self) -> None:
        # A cohort includes the subject year, which is never strictly lower
        # than itself, so 100% is unreachable. Seeing it means ties were
        # counted as lower and every tied year outranks the others.
        issues = validate_context_payload(
            self._payload_with(
                _comparison_statement(
                    value=100,
                    text=(
                        "The selected date occurred during a year with 46 "
                        "active state-based conflicts, ranking higher than "
                        "100% of 80 supported years (1946\u20132025). This "
                        "comparison describes the year, not this specific day."
                    ),
                )
            )
        )
        assert any("no year can" in issue for issue in issues), issues

    def test_a_comparison_claiming_severity_is_caught(self) -> None:
        issues = validate_context_payload(
            self._payload_with(
                _comparison_statement(
                    text=(
                        "Day Perspective compares this: 1952 was a worse year "
                        "than the median. This is a count of distinct "
                        "conflicts, not a measure of their scale."
                    )
                )
            )
        )
        assert any("implies 'worse'" in issue for issue in issues), issues

    def test_a_comparison_without_the_scale_disclaimer_is_caught(self) -> None:
        issues = validate_context_payload(
            self._payload_with(
                _comparison_statement(
                    text=(
                        "Day Perspective compares this: UCDP/PRIO records 46 "
                        "state-based armed conflicts as active in 1952, 10 "
                        "conflicts more than the 1946–2025 median of 36."
                    )
                )
            )
        )
        assert any("not a measure of" in issue for issue in issues), issues

    def test_the_context_rules_do_not_fire_on_a_comparison(self) -> None:
        # The comparison mentions the same conflicts but is a different claim
        # with a different shape. Validating it under the annual-context rules
        # reported three defects in a statement that had none.
        issues = validate_context_payload(
            self._payload_with(_comparison_statement())
        )
        assert not any("period_context marker" in issue for issue in issues), issues
        assert not any("names no year" in issue for issue in issues), issues
