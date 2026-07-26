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

import pytest
from sqlalchemy.orm import Session

from app.batch_publication import outstanding_dates, run_context_batch
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
    section_states: dict[str, object] | None = None,
) -> dict[str, object]:
    sections: dict[str, list[dict[str, object]]] = {
        "recorded_on_this_date": [],
        "typical_day_in_this_year": (
            [_daily_statement()] if typical is None else typical
        ),
        "wider_historical_context": [],
        "curated_claims": [],
        "derived_comparisons": [],
        "wonder_and_progress": [],
        "evidence_notes": [],
    }
    supported = {"recorded_on_this_date", "typical_day_in_this_year"}
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
        payload["publication_tier"] = "reviewed_enriched"

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
