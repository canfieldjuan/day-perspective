from copy import deepcopy
from datetime import date
from typing import Any

import pytest

from app import golden_canary, publish_cli


def sparse_profile() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "date": "1921-03-15",
        "profile_type": "limited_historical",
        "publication_tier": "enriched",
        "sections": {
            "recorded_on_this_date": [
                {"statement_id": "event", "statement": "A reviewed source names this event."}
            ]
        },
        "section_states": {"recorded_on_this_date": {"status": "available"}},
    }


def test_sparse_recorded_profile_passes_without_weakening_context_canary() -> None:
    payload = sparse_profile()
    before = deepcopy(payload)
    validator = getattr(golden_canary, "validate_recorded_payload", golden_canary.validate_context_payload)
    assert validator(payload) == []
    assert any("missing from sections" in issue for issue in golden_canary.validate_context_payload(payload))
    assert payload == before


@pytest.mark.parametrize("value", [None, 0, False, "", [], {}, [None], [False], ["event"], [{"statement_id": "event", "statement": ""}]])
def test_missing_or_malformed_recorded_events_fail(value: object) -> None:
    payload = sparse_profile()
    payload["sections"]["recorded_on_this_date"] = value
    assert golden_canary.validate_recorded_payload(payload)


@pytest.mark.parametrize("value", ["1899-12-31", "2026-01-01", "not-a-date", None])
def test_invalid_public_date_fails(value: object) -> None:
    payload = sparse_profile()
    payload["date"] = value
    assert golden_canary.validate_recorded_payload(payload)


def test_wrong_era_and_later_sparse_profile_fail() -> None:
    payload = sparse_profile()
    payload["profile_type"] = "standard_statistical"
    assert golden_canary.validate_recorded_payload(payload)
    payload["date"] = "1977-06-15"
    assert any("missing from sections" in x for x in golden_canary.validate_recorded_payload(payload))


@pytest.mark.parametrize("value", ["1900-01-01", "1949-12-31"])
def test_early_era_boundaries_accept_valid_sparse_profiles(value: str) -> None:
    payload = sparse_profile()
    payload["date"] = value
    assert golden_canary.validate_recorded_payload(payload) == []


def test_later_era_boundary_keeps_complete_context_requirement() -> None:
    payload = sparse_profile()
    payload.update(date="1950-01-01", profile_type="standard_statistical")
    assert golden_canary.validate_recorded_payload(payload)


def test_maximum_public_date_can_pass_with_complete_sections() -> None:
    payload = sparse_profile()
    payload.update(date="2025-12-31", profile_type="enhanced_structured")
    for key in golden_canary.SECTION_KEYS:
        if key != "recorded_on_this_date":
            payload["sections"][key] = []
            payload["section_states"][key] = {"status": "not_yet_supported", "reason": "No evidence in this publication."}
    assert golden_canary.validate_recorded_payload(payload) == []


@pytest.mark.parametrize("mutation", ["state", "unknown", "mixed", "unsupported", "tier"])
def test_sparse_mode_does_not_hide_included_content_defects(mutation: str) -> None:
    payload = sparse_profile()
    if mutation == "state":
        payload["section_states"] = {}
    elif mutation == "unknown":
        payload["sections"]["invented"] = []
    elif mutation == "mixed":
        payload["sections"]["recorded_on_this_date"].append(None)
    elif mutation == "unsupported":
        payload["section_states"]["recorded_on_this_date"] = {"status": "not_yet_supported", "reason": "Unavailable"}
    else:
        payload["publication_tier"] = "context_only"
    assert golden_canary.validate_recorded_payload(payload)


def test_included_annual_statistics_still_get_meaning_checks() -> None:
    payload = sparse_profile()
    payload["sections"]["typical_day_in_this_year"] = [{
        "statement_id": "average-daily-births",
        "statement": "Average daily births in 1921: about 1,000. This is an average daily equivalent based on the annual total, not an observation for March 15.",
        "provenance_note": "Annual total divided by 366 days; not date-specific.",
        "details": {"temporal_assignment": "uniform_period_allocation", "days_in_year": 366, "data_status": "estimated"},
    }]
    payload["section_states"]["typical_day_in_this_year"] = {"status": "available"}
    assert any("365" in x for x in golden_canary.validate_recorded_payload(payload))


def test_recorded_reader_checks_requested_date_and_cli_is_read_only(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    class ReadOnlySession:
        def __enter__(self) -> "ReadOnlySession":
            return self

        def __exit__(self, *args: object) -> None:
            pass

    seen: list[date] = []

    def read_payload(session: object, *, store: object, profile_date: date) -> dict[str, Any]:
        seen.append(profile_date)
        return sparse_profile()

    monkeypatch.setattr(golden_canary.CanaryValidation, "read_payload", staticmethod(read_payload))
    monkeypatch.setattr(publish_cli, "SessionLocal", ReadOnlySession)
    monkeypatch.setattr("sys.argv", ["publish-cli", "recorded-canary", "--date", "1921-03-15"])
    publish_cli.main()
    assert seen == [date(1921, 3, 15)]
    assert "validated=1 missing=0 dates_with_issues=0" in capsys.readouterr().out

    result = golden_canary.CanaryValidation.of(ReadOnlySession(), store=None, dates=[date(1921, 3, 16)], recorded_events=True)  # type: ignore[arg-type]
    assert not result.clean
    assert any("requested date" in x for x in result.issues["1921-03-16"])
