"""Wikidata's P585 day resolves through the shared temporal resolver (A2b, #121).

`resolve_wikidata_event` used to read the P585 day with `date.fromisoformat`,
ignoring `calendarmodel` (#114): a Julian-flagged value was read as though its
digits were Gregorian, off by up to 13 days in the supported range and silent
about it. These tests pin the primitive that replaced that -- it establishes
the convention from `calendarmodel` and resolves the day through `resolve_day`,
so a non-Gregorian calendar fails closed rather than being misread.

No database: the primitive is pure, so its contract is checkable without one.
The end-to-end resolve is covered by the DB-bound test in
`test_wikidata_resolve.py`, which CI runs.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.temporal import UnresolvedDay
from app.wikidata import _resolve_occurrence_date

GREGORIAN = "http://www.wikidata.org/entity/Q1985727"
JULIAN = "http://www.wikidata.org/entity/Q1985786"


def _p585(
    time: str, *, precision: int = 11, calendarmodel: str | None = GREGORIAN
) -> dict[str, object]:
    """A P585 time value in Wikidata's shape.

    `calendarmodel=None` omits the key, standing for a value that never carried
    one -- which must fail closed, not fall back to Gregorian.
    """
    value: dict[str, object] = {
        "time": time,
        "timezone": 0,
        "before": 0,
        "after": 0,
        "precision": precision,
    }
    if calendarmodel is not None:
        value["calendarmodel"] = calendarmodel
    return value


class TestGregorianResolvesUnchanged:
    """The common path: a Gregorian-flagged day is its own profile date."""

    def test_a_gregorian_day_is_its_own_profile_date(self) -> None:
        resolved = _resolve_occurrence_date(_p585("+1964-03-27T00:00:00Z"))
        assert resolved == date(1964, 3, 27)


class TestNonGregorianCalendarsFailClosed:
    """A calendar the resolver does not restate is refused, not misread."""

    def test_a_julian_day_is_refused_not_read_as_gregorian(self) -> None:
        """The October Revolution: Julian 1917-10-25 is Gregorian 1917-11-07.

        The old parser returned date(1917, 10, 25) -- the digits read as
        Gregorian, 13 days wrong. This asserts the distinction non-vacuously:
        the same digits resolve to 1917-10-25 under a Gregorian flag, and are
        refused (not silently that day) under a Julian one.
        """
        assert _resolve_occurrence_date(
            _p585("+1917-10-25T00:00:00Z", calendarmodel=GREGORIAN)
        ) == date(1917, 10, 25)
        with pytest.raises(UnresolvedDay):
            _resolve_occurrence_date(
                _p585("+1917-10-25T00:00:00Z", calendarmodel=JULIAN)
            )

    def test_an_unknown_calendar_model_is_refused(self) -> None:
        with pytest.raises(ValueError):
            _resolve_occurrence_date(
                _p585(
                    "+1964-03-27T00:00:00Z",
                    calendarmodel="http://www.wikidata.org/entity/Q9999999",
                )
            )

    def test_an_absent_calendar_model_is_refused(self) -> None:
        """Never presume Gregorian for a value that did not declare it."""
        with pytest.raises(ValueError):
            _resolve_occurrence_date(
                _p585("+1964-03-27T00:00:00Z", calendarmodel=None)
            )


class TestPrecisionIsStillEnforced:
    """The day-precision guard that predates A2b is preserved."""

    def test_a_coarser_precision_is_refused(self) -> None:
        with pytest.raises(ValueError):
            _resolve_occurrence_date(
                _p585("+1964-03-00T00:00:00Z", precision=10)
            )

    def test_a_malformed_time_is_refused(self) -> None:
        with pytest.raises(ValueError):
            _resolve_occurrence_date(_p585("not-a-timestamp"))
