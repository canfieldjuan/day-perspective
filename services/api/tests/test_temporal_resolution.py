"""The shared temporal resolver, against docs/PRODUCT_CONTRACT.md (D049).

The contract states a test rather than a list of conventions: a stated day
yields a date-specific event only where that convention, applied at the place
of occurrence, makes the day denote exactly one local civil day. These tests
are that criterion, case by case, plus the instant cases and the interval
guard #109 requires so A2 cannot settle #113 by implementation.

No database: the resolver is pure so the contract is checkable without one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.models import TemporalAssignment
from app.temporal import (
    CalendarSystem,
    CivilDate,
    DayConvention,
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


class TestCalendarSystemIsRestatedExactly:
    """"A day differing only by calendar system ... denotes the same local civil
    day under another name." """

    def test_julian_civil_date_restates_onto_the_gregorian_axis(self) -> None:
        """The October Revolution: Julian 1917-10-25 is Gregorian 1917-11-07.

        Thirteen days in the 20th century, and inside the supported range
        (PRODUCT_CONTRACT.md:25 opens at 1900-01-01; Russia used the Julian
        calendar until February 1918).
        """
        resolved = resolve_day(
            stated_day=date(1917, 10, 25),
            convention=JULIAN_LOCAL,
            timezone_name="Europe/Moscow",
        )
        assert resolved.profile_date == date(1917, 11, 7)

    def test_restatement_is_reported_not_derived(self) -> None:
        """Renaming a day is not deriving one, so no precision was invented."""
        resolved = resolve_day(
            stated_day=date(1917, 10, 25),
            convention=JULIAN_LOCAL,
            timezone_name="Europe/Moscow",
        )
        assert resolved.temporal_assignment is TemporalAssignment.REPORTED
        assert resolved.exact_timestamp is None
        assert resolved.utc_offset_minutes is None

    def test_the_source_calendar_and_the_restatement_are_recorded(self) -> None:
        """"the published record carries the source's calendar system and the
        fact that the day was restated." """
        resolved = resolve_day(
            stated_day=date(1917, 10, 25),
            convention=JULIAN_LOCAL,
            timezone_name="Europe/Moscow",
        )
        assert resolved.source_calendar is CalendarSystem.JULIAN
        assert "1917-10-25" in resolved.interpretation
        assert "Julian" in resolved.interpretation

    def test_a_gregorian_day_records_no_restatement(self) -> None:
        resolved = resolve_day(
            stated_day=date(1964, 3, 27), convention=GREGORIAN_LOCAL
        )
        assert resolved.source_calendar is None


class TestUtcDayTurnsOnWhetherTheIntervalsCoincide:
    """"A day whose meridian differs ... denotes a twenty-four hour interval
    that need not coincide with a local civil day." """

    def test_refused_where_the_interval_straddles_two_local_days(self) -> None:
        """1964-03-28 UTC is local 03-27 14:00 -> 03-28 14:00 in Alaska.

        It covers part of each, so choosing one would invent precision the
        source never stated.
        """
        with pytest.raises(UnresolvedDay):
            resolve_day(
                stated_day=date(1964, 3, 28),
                convention=GREGORIAN_UTC,
                timezone_name="America/Anchorage",
            )

    def test_accepted_where_the_place_sits_at_zero_offset_all_day(self) -> None:
        """"Where the interval does coincide, it denotes that day."

        London in January is UTC+0 for the whole day, so the UTC day and the
        local civil day have identical boundaries and nothing needs resolving.
        """
        resolved = resolve_day(
            stated_day=date(1964, 1, 15),
            convention=GREGORIAN_UTC,
            timezone_name="Europe/London",
        )
        assert resolved.profile_date == date(1964, 1, 15)
        assert resolved.temporal_assignment is TemporalAssignment.REPORTED

    def test_refused_for_the_same_place_under_summer_time(self) -> None:
        """Coincidence is a property of the date, not of the place.

        London in July is UTC+1, so the same location that passes in January
        fails here. This is why the rule is a test rather than a list.
        """
        with pytest.raises(UnresolvedDay):
            resolve_day(
                stated_day=date(1964, 7, 15),
                convention=GREGORIAN_UTC,
                timezone_name="Europe/London",
            )

    def test_refused_without_a_timezone_to_test_against(self) -> None:
        """The criterion is applied "at the place of occurrence"; with no
        place there is nothing to apply it to."""
        with pytest.raises(UnresolvedDay):
            resolve_day(stated_day=date(1964, 3, 28), convention=GREGORIAN_UTC)

    def test_a_contained_utc_day_denotes_the_day_it_falls_within(self) -> None:
        """Containment is enough; exact coincidence is not required.

        Kwajalein crossed the date line backward on 1969-09-30 (+11 to -12),
        so local 1969-09-30 ran about 47 hours and the UTC day sits inside it
        without sharing its boundaries. Every instant of that UTC day is
        nonetheless on local 09-30, so the day is determinate and nothing is
        invented by filing it there. Refusing would lose a real event to a
        representation quirk rather than to an evidence gap.
        """
        resolved = resolve_day(
            stated_day=date(1969, 9, 30),
            convention=GREGORIAN_UTC,
            timezone_name="Pacific/Kwajalein",
        )
        assert resolved.profile_date == date(1969, 9, 30)


class TestAnInstantResolvesAStraddlingUtcDay:
    """"it yields no date-specific event unless other evidence -- an instant,
    or the source's own statement of the local day -- resolves it to one."

    A straddling UTC day is ambiguous on its own. An instant inside it is not.
    """

    def test_the_instant_resolves_what_the_utc_day_alone_could_not(self) -> None:
        """UTC day 1964-03-28 straddles two Alaska days, but the earthquake
        instant inside it lands unambiguously on local 1964-03-27."""
        resolved = resolve_day(
            stated_day=date(1964, 3, 28),
            convention=GREGORIAN_UTC,
            instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=UTC),
            timezone_name="America/Anchorage",
        )
        assert resolved.profile_date == date(1964, 3, 27)

    def test_the_day_is_derived_because_the_instant_supplied_it(self) -> None:
        """The source never stated this local day, so it is not reported.

        Contrast the stated-local-day case, where the source did state it and
        the day stays REPORTED with the instant merely preserved.
        """
        resolved = resolve_day(
            stated_day=date(1964, 3, 28),
            convention=GREGORIAN_UTC,
            instant=datetime(1964, 3, 28, 3, 36, 14, tzinfo=UTC),
            timezone_name="America/Anchorage",
        )
        assert resolved.temporal_assignment is TemporalAssignment.DIRECT_RECORD
        assert resolved.timezone_name == "America/Anchorage"
        assert resolved.utc_offset_minutes == -600

    def test_an_instant_outside_the_stated_day_is_contradictory(self) -> None:
        """An instant is evidence about the stated day only if it is in it.

        One falling elsewhere means the source disagrees with itself, which is
        not something to resolve by preferring one half.
        """
        with pytest.raises(UnresolvedDay, match="outside"):
            resolve_day(
                stated_day=date(1964, 3, 28),
                convention=GREGORIAN_UTC,
                instant=datetime(1964, 4, 15, 3, 36, 14, tzinfo=UTC),
                timezone_name="America/Anchorage",
            )

    def test_still_refused_when_no_instant_accompanies_it(self) -> None:
        """Without the resolving evidence the refusal stands."""
        with pytest.raises(UnresolvedDay):
            resolve_day(
                stated_day=date(1964, 3, 28),
                convention=GREGORIAN_UTC,
                timezone_name="America/Anchorage",
            )


class TestTheSourcesOwnLocalDayAlsoResolvesAStraddlingUtcDay:
    """The contract names two kinds of resolving evidence -- "an instant, or
    the source's own statement of the local day". The instant is covered
    above; this is the other one.

    A source giving both a UTC day and its own local day is the case where
    the two can disagree, which is worth catching rather than losing.
    """

    def test_the_stated_local_day_resolves_what_the_utc_day_could_not(
        self,
    ) -> None:
        resolved = resolve_day(
            stated_day=date(1964, 3, 28),
            convention=GREGORIAN_UTC,
            stated_local_day=date(1964, 3, 27),
            timezone_name="America/Anchorage",
        )
        assert resolved.profile_date == date(1964, 3, 27)

    def test_it_stays_reported_because_the_source_stated_it(self) -> None:
        """Unlike the instant path, no day was derived here: the source said
        which local day it was."""
        resolved = resolve_day(
            stated_day=date(1964, 3, 28),
            convention=GREGORIAN_UTC,
            stated_local_day=date(1964, 3, 27),
            timezone_name="America/Anchorage",
        )
        assert resolved.temporal_assignment is TemporalAssignment.REPORTED
        assert resolved.timezone_name is None
        assert resolved.utc_offset_minutes is None

    def test_a_local_day_the_utc_day_never_touches_is_contradictory(self) -> None:
        """UTC 1964-03-28 covers local 03-27 and 03-28 in Alaska and nothing
        else, so a source also claiming 04-15 disagrees with itself."""
        with pytest.raises(UnresolvedDay, match="disagrees"):
            resolve_day(
                stated_day=date(1964, 3, 28),
                convention=GREGORIAN_UTC,
                stated_local_day=date(1964, 4, 15),
                timezone_name="America/Anchorage",
            )

    def test_a_disagreement_is_caught_even_when_the_utc_day_resolves(self) -> None:
        """The cross-check is the point, so it applies when the UTC day is
        unambiguous too -- that is where a silent contradiction would hide."""
        with pytest.raises(UnresolvedDay, match="disagrees"):
            resolve_day(
                stated_day=date(1964, 1, 15),
                convention=GREGORIAN_UTC,
                stated_local_day=date(1964, 1, 16),
                timezone_name="Europe/London",
            )

    def test_agreement_resolves_normally(self) -> None:
        resolved = resolve_day(
            stated_day=date(1964, 1, 15),
            convention=GREGORIAN_UTC,
            stated_local_day=date(1964, 1, 15),
            timezone_name="Europe/London",
        )
        assert resolved.profile_date == date(1964, 1, 15)

    def test_the_provenance_does_not_claim_a_containment_that_is_false(
        self,
    ) -> None:
        """UTC 1964-03-28 straddles two Alaska days, so the record must not
        say every instant of it falls on 03-27. The source's own local-day
        statement picked the day; the UTC day did not."""
        resolved = resolve_day(
            stated_day=date(1964, 3, 28),
            convention=GREGORIAN_UTC,
            stated_local_day=date(1964, 3, 27),
            timezone_name="America/Anchorage",
        )
        assert "Every instant" not in resolved.interpretation
        assert "1964-03-28" in resolved.interpretation
        assert "1964-03-27" in resolved.interpretation

    def test_a_stated_local_day_alone_is_just_a_local_day(self) -> None:
        resolved = resolve_day(
            stated_local_day=date(1964, 3, 27), convention=GREGORIAN_LOCAL
        )
        assert resolved.profile_date == date(1964, 3, 27)
        assert resolved.temporal_assignment is TemporalAssignment.REPORTED


class TestAJulianDateIsNotAGregorianDate:
    """`datetime.date` is a Gregorian type, so it cannot carry every Julian
    date. A calendar-neutral `CivilDate` can, validated per convention."""

    def test_a_julian_only_date_can_be_expressed_and_resolved(self) -> None:
        """1900 is a leap year in the Julian calendar and not in the Gregorian.

        So Julian 1900-02-29 exists, `date(1900, 2, 29)` raises, and the day
        maps to Gregorian 1900-03-13 -- inside the supported range. Carrying a
        stated Julian day in a Gregorian type makes that date unexpressible.
        """
        resolved = resolve_day(
            stated_day=CivilDate(1900, 2, 29), convention=JULIAN_LOCAL
        )
        assert resolved.profile_date == date(1900, 3, 13)

    def test_a_gregorian_date_is_still_accepted_directly(self) -> None:
        """The common case stays a plain `date`; nothing has to be wrapped."""
        resolved = resolve_day(
            stated_day=date(1964, 3, 27), convention=GREGORIAN_LOCAL
        )
        assert resolved.profile_date == date(1964, 3, 27)

    def test_a_date_impossible_in_the_stated_calendar_is_refused(self) -> None:
        """Julian February never has 30 days."""
        with pytest.raises(UnresolvedDay):
            resolve_day(
                stated_day=CivilDate(1900, 2, 30), convention=JULIAN_LOCAL
            )

    def test_a_julian_only_date_declared_gregorian_is_refused(self) -> None:
        """The same triple is valid Julian and invalid Gregorian, so the
        convention decides whether it is a date at all."""
        with pytest.raises(UnresolvedDay):
            resolve_day(
                stated_day=CivilDate(1900, 2, 29), convention=GREGORIAN_LOCAL
            )


class TestTheInterpretationStatesWhatTheSourceActuallySaid:
    """Provenance text is published, so it may not describe a stated UTC day
    as a stated local civil day."""

    def test_a_utc_day_is_not_described_as_a_local_civil_day(self) -> None:
        resolved = resolve_day(
            stated_day=date(1964, 1, 15),
            convention=GREGORIAN_UTC,
            timezone_name="Europe/London",
        )
        assert "UTC" in resolved.interpretation
        assert "as its local civil day" not in resolved.interpretation

    def test_the_stated_meridian_is_recorded(self) -> None:
        """Nothing else on the result retains it, so it would be lost."""
        resolved = resolve_day(
            stated_day=date(1964, 1, 15),
            convention=GREGORIAN_UTC,
            timezone_name="Europe/London",
        )
        assert resolved.source_meridian is Meridian.UTC

    def test_a_local_day_records_no_meridian_restatement(self) -> None:
        resolved = resolve_day(
            stated_day=date(1964, 3, 27), convention=GREGORIAN_LOCAL
        )
        assert resolved.source_meridian is None

    def test_julian_and_utc_together_both_appear(self) -> None:
        """Both axes moved, so the record has to say both."""
        resolved = resolve_day(
            stated_day=CivilDate(1964, 1, 2),
            convention=DayConvention(
                calendar=CalendarSystem.JULIAN, meridian=Meridian.UTC
            ),
            timezone_name="Europe/London",
        )
        assert resolved.profile_date == date(1964, 1, 15)
        assert "Julian" in resolved.interpretation
        assert "UTC" in resolved.interpretation
        assert resolved.source_calendar is CalendarSystem.JULIAN
        assert resolved.source_meridian is Meridian.UTC


class TestUnestablishedConventionDenotesNothing:
    """"A day whose convention is not established denotes nothing determinate,
    and yields no date-specific event." """

    def test_refused_when_no_convention_is_declared(self) -> None:
        with pytest.raises(UnresolvedDay):
            resolve_day(
                stated_day=date(1964, 3, 27), timezone_name="America/Anchorage"
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


class TestIntervalIsRefusedRatherThanCollapsed:
    """#109's interim guard for #113.

    governance.py:985 admits an event to a profile only on its start_date, so
    a multi-day interval silently becomes a start-day event. Which profile(s)
    an interval belongs to is undecided (#113); the resolver must fail loudly
    rather than settle it by taking start_date.
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

    def test_the_refusal_names_the_undecided_policy(self) -> None:
        """A reader hitting this must find the open question, not a puzzle."""
        with pytest.raises(UnresolvedDay, match="interval"):
            resolve_day(
                stated_day=date(1964, 3, 27),
                stated_day_end=date(1964, 3, 29),
                convention=GREGORIAN_LOCAL,
            )


class TestNothingStatedYieldsNothing:
    def test_refused_when_neither_a_day_nor_an_instant_is_given(self) -> None:
        with pytest.raises(UnresolvedDay):
            resolve_day(timezone_name="America/Anchorage")
