"""The shared temporal resolver, against docs/PRODUCT_CONTRACT.md (D049).

The contract states a test rather than a list of conventions: a stated day
yields a date-specific event only where that convention, applied at the place
of occurrence, makes the day denote exactly one local civil day.

This slice implements that test for the two routes publishers in this tree
reach -- a stated local civil day (Wikidata) and an instant (USGS, today at
usgs.py:244) -- and refuses the rest by name. So these tests come in two
kinds, and the second kind is not filler: a refusal that silently misreads a
day is worse than one that raises, so each deferred convention is tested for
refusing AND for not quietly resolving to the wrong day.

Stated UTC calendar days are #120, Julian dates #114, occurrence intervals
#113.

No database: the resolver is pure so the contract is checkable without one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, tzinfo
from zoneinfo import ZoneInfo

import pytest

from app.models import TemporalAssignment
from app.temporal import (
    CalendarSystem,
    DayConvention,
    HowAssigned,
    Meridian,
    UnresolvedDay,
    resolve_day,
)

GREGORIAN_LOCAL = DayConvention(
    calendar=CalendarSystem.GREGORIAN, meridian=Meridian.LOCAL_CIVIL
)
JULIAN_LOCAL = DayConvention(
    calendar=CalendarSystem.JULIAN, meridian=Meridian.LOCAL_CIVIL
)
GREGORIAN_UTC = DayConvention(
    calendar=CalendarSystem.GREGORIAN, meridian=Meridian.UTC
)


class _NoOffset(tzinfo):
    """A tzinfo that supplies no offset, which leaves a datetime naive.

    Python's own test for awareness is `tzinfo is not None AND
    utcoffset() is not None`; this is the second half, which a guard checking
    only the first half misses.
    """

    def utcoffset(self, dt: datetime | None) -> None:
        return None

    def dst(self, dt: datetime | None) -> None:
        return None

    def tzname(self, dt: datetime | None) -> None:
        return None


class TestStatedLocalDayDenotesItself:
    """"A day already stated as the conventional local civil day denotes itself.
    It is taken as reported and is never re-derived." """

    def test_is_reported_and_unchanged(self) -> None:
        resolved = resolve_day(
            stated_day=date(1964, 3, 27),
            convention=GREGORIAN_LOCAL,
            timezone_name="America/Anchorage",
        )
        assert resolved.profile_date == date(1964, 3, 27)
        assert resolved.temporal_assignment is TemporalAssignment.REPORTED
        assert resolved.how_assigned is HowAssigned.STATED_LOCAL_DAY

    def test_derivation_fields_stay_absent(self) -> None:
        """Nothing was derived, so the derivation record is empty.

        Not an oversight: the contract records what a source stated separately
        from what the product derived, field by field.
        """
        resolved = resolve_day(
            stated_day=date(1964, 3, 27),
            convention=GREGORIAN_LOCAL,
            timezone_name="America/Anchorage",
        )
        assert resolved.exact_timestamp is None
        assert resolved.timezone_name is None
        assert resolved.utc_offset_minutes is None

    def test_resolves_without_a_timezone(self) -> None:
        """A reported local day needs no timezone; nothing is being converted."""
        resolved = resolve_day(
            stated_day=date(1964, 3, 27), convention=GREGORIAN_LOCAL
        )
        assert resolved.profile_date == date(1964, 3, 27)


class TestUnestablishedConventionDenotesNothing:
    """"A day whose convention is not established denotes nothing determinate,
    and yields no date-specific event." """

    def test_refused_when_no_convention_is_declared(self) -> None:
        with pytest.raises(UnresolvedDay):
            resolve_day(
                stated_day=date(1964, 3, 27), timezone_name="America/Anchorage"
            )

    def test_the_refusal_says_the_adapter_must_declare_one(self) -> None:
        """"neither is presumed" -- and 1917-10-25 is where presuming bites:
        Gregorian it is itself, Julian it is 1917-11-07."""
        with pytest.raises(UnresolvedDay, match="declares"):
            resolve_day(stated_day=date(1917, 10, 25))


class TestAConventionDescribesAStatedDay:
    """A convention supplied without a day describes nothing.

    Publishing one anyway was #119's round-5 finding: a caller passing only an
    instant, plus a convention, got a record asserting the source had stated a
    day on a meridian it never mentioned. Refusing the input closes the class
    rather than filtering the output.
    """

    def test_a_convention_without_a_day_is_refused(self) -> None:
        with pytest.raises(UnresolvedDay, match="stated none"):
            resolve_day(
                convention=GREGORIAN_UTC,
                instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=UTC),
                timezone_name="America/Anchorage",
            )

    def test_the_same_call_without_the_convention_resolves(self) -> None:
        """The contrast: it is the dangling convention that is refused, not the
        instant."""
        resolved = resolve_day(
            instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=UTC),
            timezone_name="America/Anchorage",
        )
        assert resolved.profile_date == date(1964, 3, 27)


class TestDeferredConventionsAreRefusedNotMisread:
    """A convention this slice does not implement must raise, not guess.

    Each case pairs the refusal with the wrong answer it would otherwise give,
    because a silent misread is the failure that matters. Both name their issue
    so a reader hitting one finds the open work rather than a puzzle.
    """

    def test_a_utc_calendar_day_is_refused(self) -> None:
        with pytest.raises(UnresolvedDay, match="#120"):
            resolve_day(
                stated_day=date(1964, 3, 28),
                convention=GREGORIAN_UTC,
                timezone_name="America/Anchorage",
            )

    def test_a_utc_calendar_day_is_not_read_as_a_local_day(self) -> None:
        """1964-03-28 UTC is local 03-27 14:00 -> 03-28 14:00 in Alaska, so it
        denotes no single local day. Filing it as local 03-28 would invent a
        day the source never stated."""
        with pytest.raises(UnresolvedDay):
            resolve_day(
                stated_day=date(1964, 3, 28),
                convention=GREGORIAN_UTC,
                timezone_name="America/Anchorage",
            )

    def test_a_julian_date_is_refused(self) -> None:
        with pytest.raises(UnresolvedDay, match="#114"):
            resolve_day(
                stated_day=date(1917, 10, 25),
                convention=JULIAN_LOCAL,
                timezone_name="Europe/Moscow",
            )

    def test_a_julian_date_is_not_read_as_a_gregorian_one(self) -> None:
        """The October Revolution: Julian 1917-10-25 is Gregorian 1917-11-07.
        Thirteen days, so reading the triple as Gregorian files it on the wrong
        profile rather than failing."""
        with pytest.raises(UnresolvedDay):
            resolve_day(
                stated_day=date(1917, 10, 25),
                convention=JULIAN_LOCAL,
                timezone_name="Europe/Moscow",
            )


class TestInstantDerivesADay:
    """"A source that states an instant has not thereby stated a day." """

    def test_derives_the_local_civil_day(self) -> None:
        """The golden case: the 1964 Alaska earthquake occurred on March 28
        UTC and belongs to the March 27 profile in Alaska civil time (D013)."""
        resolved = resolve_day(
            instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=UTC),
            timezone_name="America/Anchorage",
        )
        assert resolved.profile_date == date(1964, 3, 27)
        assert resolved.how_assigned is HowAssigned.DERIVED_FROM_INSTANT

    def test_records_the_timezone_applied_and_the_resulting_offset(self) -> None:
        """"the published record carries the timezone applied and the
        resulting offset." """
        resolved = resolve_day(
            instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=UTC),
            timezone_name="America/Anchorage",
        )
        assert resolved.timezone_name == "America/Anchorage"
        assert resolved.utc_offset_minutes == -600
        assert resolved.exact_timestamp == datetime(
            1964, 3, 28, 3, 36, 14, tzinfo=UTC
        )

    def test_a_derived_day_is_not_reported(self) -> None:
        resolved = resolve_day(
            instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=UTC),
            timezone_name="America/Anchorage",
        )
        assert resolved.temporal_assignment is TemporalAssignment.DIRECT_RECORD

    def test_refused_when_the_place_of_occurrence_is_unknown(self) -> None:
        """"the product does not assign a day by choosing a meridian." """
        with pytest.raises(UnresolvedDay):
            resolve_day(instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=UTC))

    def test_refused_for_a_naive_instant(self) -> None:
        """An instant without an offset is not an instant."""
        with pytest.raises(UnresolvedDay):
            resolve_day(
                instant=datetime(1964, 3, 28, 3, 36, 14),
                timezone_name="America/Anchorage",
            )

    def test_refused_for_a_tzinfo_that_supplies_no_offset(self) -> None:
        """Python calls a value naive when utcoffset() is None, tzinfo or not.

        Testing only `tzinfo is None` let this through to astimezone(), which
        reads a naive value in the HOST process's zone -- so the same source
        data published 1964-03-27 on a UTC host and 1964-03-28 on a
        Pacific/Kiritimati one. A date that depends on which machine ingested
        it is the failure this module exists to prevent.
        """
        with pytest.raises(UnresolvedDay, match="naive"):
            resolve_day(
                instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=_NoOffset()),
                timezone_name="America/Anchorage",
            )

    def test_an_ordinary_aware_instant_is_unaffected(self) -> None:
        """The contrast: the tightened guard must not refuse a real instant."""
        resolved = resolve_day(
            instant=datetime(
                1964, 3, 28, 3, 36, 14, tzinfo=ZoneInfo("America/Anchorage")
            ),
            timezone_name="America/Anchorage",
        )
        assert resolved.profile_date == date(1964, 3, 28)

    def test_refused_for_an_unknown_timezone(self) -> None:
        with pytest.raises(UnresolvedDay, match="IANA"):
            resolve_day(
                instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=UTC),
                timezone_name="Mars/Olympus_Mons",
            )


class TestStatedDayAndInstantTogether:
    """"A source may state both a local civil day and an instant; the day is
    then reported and the instant is preserved as stated, neither re-derived
    nor discarded." """

    def test_the_day_is_reported_not_re_derived(self) -> None:
        """The instant would derive 03-27 in Alaska, but the source stated
        03-28 as its local day. The stated day wins; it is never re-derived."""
        resolved = resolve_day(
            stated_day=date(1964, 3, 28),
            convention=GREGORIAN_LOCAL,
            instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=UTC),
            timezone_name="America/Anchorage",
        )
        assert resolved.profile_date == date(1964, 3, 28)
        assert resolved.temporal_assignment is TemporalAssignment.REPORTED

    def test_the_instant_is_preserved(self) -> None:
        """exact_timestamp records what the source STATED, so it survives a
        reported day. Discarding it loses precision the source supplied."""
        resolved = resolve_day(
            stated_day=date(1964, 3, 28),
            convention=GREGORIAN_LOCAL,
            instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=UTC),
            timezone_name="America/Anchorage",
        )
        assert resolved.exact_timestamp == datetime(
            1964, 3, 28, 3, 36, 14, tzinfo=UTC
        )

    def test_no_offset_is_recorded_for_a_reported_day(self) -> None:
        """timezone_name and utc_offset_minutes record how a day was DERIVED.
        No day was derived here, so there is nothing to record."""
        resolved = resolve_day(
            stated_day=date(1964, 3, 28),
            convention=GREGORIAN_LOCAL,
            instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=UTC),
            timezone_name="America/Anchorage",
        )
        assert resolved.timezone_name is None
        assert resolved.utc_offset_minutes is None


class TestEveryPathRecordsWhatTheSourceSaid:
    """Provenance is built once, from the whole statement, on every route.

    Consecutive review rounds each found a provenance defect, several created
    by the previous round's fix, because the text was assembled at the end from
    whatever the resolving branch had in scope. These pin the property that
    replaced that: no path omits an input it did not use.
    """

    def test_a_reported_day_names_the_day_and_says_it_was_not_derived(
        self,
    ) -> None:
        resolved = resolve_day(
            stated_day=date(1964, 3, 27), convention=GREGORIAN_LOCAL
        )
        assert "1964-03-27" in resolved.interpretation
        assert "local civil day" in resolved.interpretation
        assert "not re-derived" in resolved.interpretation

    def test_a_derived_day_names_the_instant_and_the_zone_applied(self) -> None:
        resolved = resolve_day(
            instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=UTC),
            timezone_name="America/Anchorage",
        )
        assert "1964-03-28T03:36:14+00:00" in resolved.interpretation
        assert "America/Anchorage" in resolved.interpretation
        assert "derived" in resolved.interpretation

    def test_a_derived_day_does_not_claim_the_source_stated_it(self) -> None:
        """The contrast that makes the previous test mean something: the day
        the product derived must not be presented as one the source said."""
        resolved = resolve_day(
            instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=UTC),
            timezone_name="America/Anchorage",
        )
        said, _, _ = resolved.interpretation.partition(". ")
        assert "1964-03-27" not in said

    def test_a_day_stated_alongside_an_instant_records_both(self) -> None:
        """The instant did not decide the day, and is recorded anyway -- a
        reader cannot check the source against itself otherwise."""
        resolved = resolve_day(
            stated_day=date(1964, 3, 28),
            convention=GREGORIAN_LOCAL,
            instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=UTC),
            timezone_name="America/Anchorage",
        )
        assert "1964-03-28 as its local civil day" in resolved.interpretation
        assert "1964-03-28T03:36:14+00:00" in resolved.interpretation

    def test_the_statement_survives_on_the_record(self) -> None:
        """Retained whole, so a reader can check the source against itself."""
        resolved = resolve_day(
            stated_day=date(1964, 3, 28),
            convention=GREGORIAN_LOCAL,
            instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=UTC),
            timezone_name="America/Anchorage",
        )
        assert resolved.statement.day == date(1964, 3, 28)
        assert resolved.statement.convention is GREGORIAN_LOCAL
        assert resolved.statement.instant is not None


class TestIntervalIsRefusedRatherThanCollapsed:
    """The interval policy, decided by D050 and enforced here.

    A multi-day occurrence yields no date-specific event: its span is recorded
    but filed under no single day, never collapsed to its start date. The
    resolver refuses it rather than let a future publisher inherit the
    start_date default (`_validated_candidates` keys featured candidates by
    start_date at governance.py:985; no publisher builds a profile from a UCDP
    interval today). Endpoint-resolution enforcement -- resolving each endpoint
    by convention, then counting distinct local civil days -- is A2c; this guard
    is the interim raw-date proxy.
    """

    def test_a_multi_day_interval_is_refused(self) -> None:
        with pytest.raises(UnresolvedDay):
            resolve_day(
                stated_day=date(1964, 3, 27),
                stated_day_end=date(1964, 3, 29),
                convention=GREGORIAN_LOCAL,
            )

    def test_a_single_day_interval_is_not_an_interval(self) -> None:
        """start == end is a day, and resolves normally."""
        resolved = resolve_day(
            stated_day=date(1964, 3, 27),
            stated_day_end=date(1964, 3, 27),
            convention=GREGORIAN_LOCAL,
        )
        assert resolved.profile_date == date(1964, 3, 27)

    def test_the_refusal_names_the_decided_policy(self) -> None:
        """A reader hitting this must find the decided policy, not a puzzle."""
        with pytest.raises(UnresolvedDay, match="D050"):
            resolve_day(
                stated_day=date(1964, 3, 27),
                stated_day_end=date(1964, 3, 29),
                convention=GREGORIAN_LOCAL,
            )

    def test_an_end_without_a_start_is_refused(self) -> None:
        with pytest.raises(UnresolvedDay):
            resolve_day(
                stated_day_end=date(1964, 3, 29), convention=GREGORIAN_LOCAL
            )

    def test_an_interval_endpoint_is_resolved_before_it_is_counted(self) -> None:
        """D050 counts distinct *local civil days*, so each endpoint is resolved
        by convention first. A Julian interval therefore refuses on the calendar
        it cannot restate (#114), not on being an interval -- the endpoint
        resolution precedes the interval count.
        """
        with pytest.raises(UnresolvedDay, match="#114"):
            resolve_day(
                stated_day=date(1917, 10, 25),
                stated_day_end=date(1917, 10, 27),
                convention=JULIAN_LOCAL,
            )


class TestNothingStatedYieldsNothing:
    def test_refused_when_neither_a_day_nor_an_instant_is_given(self) -> None:
        with pytest.raises(UnresolvedDay):
            resolve_day(timezone_name="America/Anchorage")
