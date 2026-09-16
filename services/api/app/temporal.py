"""One shared temporal resolver, per docs/PRODUCT_CONTRACT.md and D049.

An event is filed under the conventional local civil day at its place of
occurrence. Every publisher of a date-specific event resolves its day here, so
the same instant and place cannot reach different profiles depending on which
adapter ingested them.

The contract states a test rather than a list of conventions: a stated day
yields a date-specific event only where that convention, applied at the place
of occurrence, makes the day denote exactly one local civil day. This module is
that test. Each convention below is an illustration of it, not a rule of its
own -- an enumeration of conventions is open by construction, and successive
revisions of the contract were each found incomplete before it became a
criterion.

What a source stated and what the product derived are recorded separately,
field by field: `exact_timestamp` records a stated instant whether or not a day
was also stated, while `timezone_name` and `utc_offset_minutes` record how a
day was derived and are absent when none was.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import TemporalAssignment


class CalendarSystem(str, Enum):
    """The calendar system that names a day."""

    GREGORIAN = "gregorian"
    JULIAN = "julian"


class Meridian(str, Enum):
    """The meridian at which a day begins."""

    LOCAL_CIVIL = "local_civil"
    UTC = "utc"


@dataclass(frozen=True)
class CivilDate:
    """A day as a source stated it, in whatever calendar the source uses.

    `datetime.date` is a Gregorian type and rejects a triple that is not a
    Gregorian date, so it cannot carry every day a source may legitimately
    state. Julian 1900-02-29 is the plain case: 1900 is a leap year in the
    Julian calendar and not in the Gregorian, the date is real, it maps to
    Gregorian 1900-03-13 inside the supported range, and `date(1900, 2, 29)`
    raises. Whether a triple is a date at all depends on the calendar, so the
    stated day is carried calendar-neutral and validated against the stated
    convention rather than against Gregorian rules it does not follow.

    `ResolvedDay.profile_date` stays a `date`, because the product's axis is
    Gregorian by definition; only the *stated* day needs this.
    """

    year: int
    month: int
    day: int


@dataclass(frozen=True)
class DayConvention:
    """How a source states its days.

    A convention fixes both things independently; neither is presumed. An
    adapter that cannot establish both has not established the convention.
    """

    calendar: CalendarSystem
    meridian: Meridian


@dataclass(frozen=True)
class ResolvedDay:
    """One local civil day, with the record of how it was arrived at."""

    profile_date: date
    temporal_assignment: TemporalAssignment
    interpretation: str
    exact_timestamp: datetime | None = None
    timezone_name: str | None = None
    utc_offset_minutes: int | None = None
    source_calendar: CalendarSystem | None = None
    source_meridian: Meridian | None = None


class UnresolvedDay(ValueError):
    """The evidence does not denote exactly one local civil day.

    Raised rather than returning a best guess: the product refuses a
    date-specific event instead of inventing precision a source never stated.

    A `ValueError` so that callers already treating an unusable source value as
    a validation failure keep working unchanged; `except UnresolvedDay` still
    distinguishes a contract refusal, which is a normal fail-closed outcome
    rather than a defect, from an ordinary bad value.
    """


def _zone(timezone_name: str | None) -> ZoneInfo | None:
    if timezone_name is None:
        return None
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise UnresolvedDay(
            f"Timezone {timezone_name!r} is not a known IANA zone, so the "
            "place of occurrence cannot be established."
        ) from error


def _as_civil(day: date | CivilDate) -> CivilDate:
    """A stated day as a calendar-neutral triple, whichever way it was given."""
    if isinstance(day, CivilDate):
        return day
    return CivilDate(day.year, day.month, day.day)


def _is_julian_date(stated: CivilDate) -> bool:
    """Whether a triple is a real date in the Julian calendar.

    Identical to the Gregorian rules except for the leap year: Julian leaps
    every fourth year with no century exception, so 1900-02-29 is a date here
    and is not one on the Gregorian axis.
    """
    if not 1 <= stated.month <= 12 or stated.day < 1:
        return False
    lengths = (31, 29 if stated.year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return stated.day <= lengths[stated.month - 1]


def _stated_as_gregorian(stated: CivilDate, calendar: CalendarSystem) -> date:
    """The stated day on the product's Gregorian axis, or a refusal.

    Restating a Julian date renames the same local civil day rather than
    choosing between days, so it invents no precision.
    """
    if calendar is CalendarSystem.JULIAN:
        if not _is_julian_date(stated):
            raise UnresolvedDay(
                f"{stated.year:04d}-{stated.month:02d}-{stated.day:02d} is not "
                "a date in the Julian calendar the source states, so it "
                "denotes no day at all."
            )
        return _julian_to_gregorian(stated)
    try:
        return date(stated.year, stated.month, stated.day)
    except ValueError as error:
        raise UnresolvedDay(
            f"{stated.year:04d}-{stated.month:02d}-{stated.day:02d} is not a "
            "date in the Gregorian calendar the source states, so it denotes "
            "no day at all."
        ) from error


def _julian_to_gregorian(day: CivilDate) -> date:
    """Restate a Julian civil date on the Gregorian axis, exactly.

    The two calendars name the same local civil day differently, so this
    renames a day rather than choosing between days and invents no precision.
    Via Julian Day Number, which is exact for every date either calendar can
    express.
    """
    a = (14 - day.month) // 12
    y = day.year + 4800 - a
    m = day.month + 12 * a - 3
    jdn = day.day + (153 * m + 2) // 5 + 365 * y + y // 4 - 32083

    a = jdn + 32044
    b = (4 * a + 3) // 146097
    c = a - (146097 * b) // 4
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    return date(
        year=100 * b + d - 4800 + m // 10,
        month=m + 3 - 12 * (m // 10),
        day=e - (153 * m + 2) // 5 + 1,
    )


def _utc_day_denotes(day: date, zone: ZoneInfo) -> bool:
    """Whether every instant of a UTC calendar day falls on the stated local day.

    A UTC day is a twenty-four hour interval anchored elsewhere, so it need not
    line up with any local civil day. Whether it does is a property of the
    date, not of the place: a location at zero offset in winter can be an hour
    off under summer time.

    The test is containment, not coincidence. Where the UTC day sits inside a
    longer local day without sharing its boundaries -- Kwajalein crossed the
    date line backward on 1969-09-30, making local 09-30 about 47 hours long --
    every instant is still on one local day, so the day is determinate and
    nothing is invented by filing it there. Requiring identical boundaries
    would refuse a real event over a representation quirk rather than an
    evidence gap.

    Endpoints alone would be unsound. Between transitions local time is UTC
    plus a constant, so it is monotonic and the endpoints bracket every instant
    between them; across a transition it is not, and the local date could in
    principle leave and return inside the interval. So the endpoints decide
    only when the offset is unchanged, and a transition forces a scan.
    (No zone in tzdata actually does leave and return within a UTC day between
    1900 and 2025 -- checked exhaustively -- but that is a property of the
    current data, not a guarantee, and it is not what this rests on.)
    """
    return _local_dates_touched(day, zone) == {day}


def _local_dates_touched(day: date, zone: ZoneInfo) -> set[date]:
    """Every local civil date some instant of a UTC calendar day falls on.

    The fast path is sound rather than convenient: while the offset is
    unchanged across the interval, local time is UTC plus a constant and so
    monotonic, and the endpoints bracket every instant between them. Only a
    transition can break that, and only then is a scan needed.
    """
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    last = end - timedelta(microseconds=1)

    first_local = start.astimezone(zone)
    last_local = last.astimezone(zone)
    if first_local.utcoffset() == last_local.utcoffset():
        return {first_local.date(), last_local.date()}

    touched = {first_local.date(), last_local.date()}
    probe = start
    step = timedelta(minutes=15)
    while probe < end:
        touched.add(probe.astimezone(zone).date())
        probe += step
    return touched


def _resolve_instant(instant: datetime, zone: ZoneInfo, timezone_name: str) -> ResolvedDay:
    local = instant.astimezone(zone)
    offset = local.utcoffset()
    if offset is None:
        raise UnresolvedDay(
            f"No local offset could be determined for {timezone_name} at "
            f"{instant.isoformat()}, so no local civil day follows from it."
        )
    local_date = local.date()
    return ResolvedDay(
        profile_date=local_date,
        temporal_assignment=TemporalAssignment.DIRECT_RECORD,
        exact_timestamp=instant,
        timezone_name=timezone_name,
        # Truncates toward zero rather than flooring, preserving the behaviour
        # of the USGS conversion this generalizes. The two differ only for a
        # negative sub-minute offset, which historical LMT zones do have inside
        # the supported range (Europe/Moscow LMT was +02:30:17); that the field
        # cannot represent those seconds at all is filed separately.
        utc_offset_minutes=int(offset.total_seconds() / 60),
        interpretation=(
            f"The stated instant {instant.isoformat()} is assigned to "
            f"{local_date.isoformat()} under historical {timezone_name} civil-time "
            "rules. The day is derived, not reported."
        ),
    )


def resolve_day(
    *,
    stated_day: date | CivilDate | None = None,
    stated_day_end: date | CivilDate | None = None,
    convention: DayConvention | None = None,
    stated_local_day: date | CivilDate | None = None,
    instant: datetime | None = None,
    timezone_name: str | None = None,
) -> ResolvedDay:
    """Resolve what a source stated to exactly one local civil day, or refuse.

    `stated_day` is a day the source stated, in `convention`. A Gregorian day
    may be given as a plain `date`; a day in another calendar needs
    `CivilDate`, because `date` validates its argument as Gregorian and so
    cannot carry every day a source may legitimately state. `instant` is an
    instant the source stated. `stated_local_day` is the source's own
    statement of the local civil day, in the same calendar as `convention`.
    Any may be given, or several. `timezone_name` is the IANA zone at the
    place of occurrence.

    Passing more than one is how a source's statements get cross-checked
    rather than silently reconciled: where two of them disagree, the source
    disagrees with itself and neither is preferred.

    Raises `UnresolvedDay` whenever the evidence does not denote exactly one
    local civil day, which is the contract's fail-closed default rather than an
    error condition.
    """
    if instant is not None and instant.tzinfo is None:
        raise UnresolvedDay(
            "A naive datetime does not denote an instant, so no local civil "
            "day follows from it."
        )

    if stated_day_end is not None and (
        stated_day is None or _as_civil(stated_day_end) != _as_civil(stated_day)
    ):
        raise UnresolvedDay(
            f"The source states an interval ({stated_day} to {stated_day_end}), "
            "not a day. Which profile or profiles an occurrence interval "
            "belongs to is not decided by the contract; see issue #113. "
            "Refused rather than collapsed to the start date."
        )

    zone = _zone(timezone_name)

    if stated_day is None and stated_local_day is not None:
        # The source stated its local civil day and nothing that needs
        # reconciling against it, so it denotes itself.
        stated_day, convention = stated_local_day, convention or DayConvention(
            calendar=CalendarSystem.GREGORIAN, meridian=Meridian.LOCAL_CIVIL
        )
        stated_local_day = None

    if stated_day is None:
        if instant is None:
            raise UnresolvedDay(
                "The source states neither a day nor an instant, so it yields "
                "no date-specific event."
            )
        if zone is None or timezone_name is None:
            raise UnresolvedDay(
                "The instant's place of occurrence is unknown, so it yields no "
                "date-specific event: the product does not assign a day by "
                "choosing a meridian."
            )
        return _resolve_instant(instant, zone, timezone_name)

    if convention is None:
        raise UnresolvedDay(
            "The source's day convention is not established, so the stated day "
            "denotes nothing determinate and yields no date-specific event."
        )

    stated = _as_civil(stated_day)
    day = _stated_as_gregorian(stated, convention.calendar)
    restated = convention.calendar is not CalendarSystem.GREGORIAN
    local_statement: date | None = None

    if convention.meridian is Meridian.UTC:
        if zone is None or timezone_name is None:
            raise UnresolvedDay(
                f"The stated day {day.isoformat()} is a UTC calendar day, and "
                "whether it denotes one local civil day depends on the place of "
                "occurrence, which is unknown."
            )
        touched = _local_dates_touched(day, zone)

        # A source stating its own local day is cross-checked against the UTC
        # interval whether or not that interval was ambiguous. A contradiction
        # is most dangerous exactly where the UTC day looks unambiguous, since
        # nothing else would surface it.
        if stated_local_day is not None:
            local_statement = _stated_as_gregorian(
                _as_civil(stated_local_day), convention.calendar
            )
            local = local_statement
            if local not in touched:
                raise UnresolvedDay(
                    f"The source states local civil day {local.isoformat()}, "
                    f"but no instant of its stated UTC calendar day "
                    f"{day.isoformat()} falls on that day in {timezone_name}. "
                    "The source disagrees with itself and neither value "
                    "resolves the other."
                )
            day = local

        elif not _utc_day_denotes(day, zone):
            # The day alone is ambiguous, but the contract admits other
            # evidence that resolves it -- an instant inside the interval
            # lands on exactly one local civil day. The day is then derived
            # from that instant rather than reported, because the source never
            # stated the local day; it stated a UTC one and an instant.
            if instant is None:
                raise UnresolvedDay(
                    f"The UTC calendar day {day.isoformat()} falls across two "
                    f"local civil days in {timezone_name}. Choosing one would "
                    "invent precision the source never stated, so it yields no "
                    "date-specific event without further evidence."
                )
            if instant.astimezone(UTC).date() != day:
                raise UnresolvedDay(
                    f"The stated instant {instant.isoformat()} falls outside "
                    f"the stated UTC calendar day {day.isoformat()}, so the "
                    "source disagrees with itself and neither value resolves "
                    "the other."
                )
            return _resolve_instant(instant, zone, timezone_name)

    # The interpretation is published provenance, so it states what the source
    # actually said on BOTH axes. Describing a stated UTC day as a stated local
    # civil day would be a false claim in the one field a reader consults to
    # check the assignment, and no other field retains the meridian.
    stated_text = f"{stated.year:04d}-{stated.month:02d}-{stated.day:02d}"
    if convention.meridian is Meridian.UTC:
        said = f"{stated_text} as a UTC calendar day"
        if local_statement is not None:
            # That UTC day may well straddle two local days; it is the source's
            # own local-day statement that picked one, and saying otherwise
            # would assert a containment that does not hold.
            said += f" and local civil day {local_statement.isoformat()}"
            because = (
                f"The UTC day covers that local day in {timezone_name}, and the "
                "local day the source stated is the one filed."
            )
        else:
            because = (
                f"Every instant of that day falls on local {day.isoformat()} in "
                f"{timezone_name}, so it denotes that day and nothing is "
                "invented by filing it there."
            )
    else:
        said = f"{stated_text} as its local civil day"
        because = "Taken as reported and not re-derived."
    if restated:
        said += f" in the {convention.calendar.value.capitalize()} calendar"
        because = (
            f"Restated as {day.isoformat()} on the Gregorian axis, which names "
            f"the same local civil day under another calendar. {because}"
        )

    return ResolvedDay(
        profile_date=day,
        temporal_assignment=TemporalAssignment.REPORTED,
        exact_timestamp=instant,
        interpretation=f"The source states {said}. {because}",
        source_calendar=convention.calendar if restated else None,
        source_meridian=(
            convention.meridian if convention.meridian is not Meridian.LOCAL_CIVIL else None
        ),
    )
