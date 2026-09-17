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

Provenance is built the same way, and for the same reason. What the source
stated is captured once, whole, as a `SourceStatement`; every path resolves to
a `HowAssigned` and the record is constructed in exactly one place from that
pair. No path can return early past provenance construction, and no
combination of inputs can reach a branch that does not know about it. An
earlier revision assembled the text at the end from scattered locals, and four
consecutive review rounds each found a provenance defect -- three of them
introduced by the previous round's fix. That is the same failure the criterion
above exists to avoid, applied to the wrong half of the module.
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

    A `CivilDate` carries no calendar of its own. That is deliberate -- it is
    the reason the type exists -- and it means a `CivilDate` never establishes
    a convention by itself.
    """

    year: int
    month: int
    day: int

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"


@dataclass(frozen=True)
class DayConvention:
    """How a source states its days.

    A convention fixes both things independently; neither is presumed. An
    adapter that cannot establish both has not established the convention.
    """

    calendar: CalendarSystem
    meridian: Meridian


@dataclass(frozen=True)
class SourceStatement:
    """Everything the source said about when the event happened, unconverted.

    Held whole and separate from anything derived from it, so the published
    record can state what was said without a conversion standing in for it.
    The days stay as stated triples: a Julian day and its Gregorian
    equivalent are different labels and must not share a variable.
    """

    day: CivilDate | None = None
    convention: DayConvention | None = None
    local_day: CivilDate | None = None
    instant: datetime | None = None


class HowAssigned(str, Enum):
    """The route from a `SourceStatement` to one local civil day.

    Exhaustive by construction: every successful resolution is exactly one of
    these, and the record is rendered from the pair.
    """

    STATED_LOCAL_DAY = "stated_local_day"
    UTC_DAY_CONTAINED = "utc_day_contained"
    UTC_DAY_RESOLVED_BY_LOCAL_STATEMENT = "utc_day_resolved_by_local_statement"
    DERIVED_FROM_INSTANT = "derived_from_instant"


@dataclass(frozen=True)
class ResolvedDay:
    """One local civil day, with the record of how it was arrived at.

    The statement is retained, not just its outcome: a reader checking a date
    needs to see everything the source said, including the parts that did not
    decide the day, or they cannot check it against anything.
    """

    profile_date: date
    temporal_assignment: TemporalAssignment
    how_assigned: HowAssigned
    statement: SourceStatement
    interpretation: str
    exact_timestamp: datetime | None = None
    timezone_name: str | None = None
    utc_offset_minutes: int | None = None

    @property
    def source_calendar(self) -> CalendarSystem | None:
        """The stated calendar, where it was not the product's own."""
        convention = self.statement.convention
        if convention is None or convention.calendar is CalendarSystem.GREGORIAN:
            return None
        return convention.calendar

    @property
    def source_meridian(self) -> Meridian | None:
        """The stated meridian, where it was not the local civil one."""
        convention = self.statement.convention
        if convention is None or convention.meridian is Meridian.LOCAL_CIVIL:
            return None
        return convention.meridian


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


def _on_gregorian_axis(stated: CivilDate, calendar: CalendarSystem) -> date:
    """A stated triple on the product's Gregorian axis, or a refusal."""
    if calendar is CalendarSystem.JULIAN:
        if not _is_julian_date(stated):
            raise UnresolvedDay(
                f"{stated} is not a date in the Julian calendar the source "
                "states, so it denotes no day at all."
            )
        return _julian_to_gregorian(stated)
    try:
        return date(stated.year, stated.month, stated.day)
    except ValueError as error:
        raise UnresolvedDay(
            f"{stated} is not a date in the Gregorian calendar the source "
            "states, so it denotes no day at all."
        ) from error


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


def _offset_minutes(instant: datetime, zone: ZoneInfo, timezone_name: str) -> tuple[date, int]:
    local = instant.astimezone(zone)
    offset = local.utcoffset()
    if offset is None:
        raise UnresolvedDay(
            f"No local offset could be determined for {timezone_name} at "
            f"{instant.isoformat()}, so no local civil day follows from it."
        )
    # Truncates toward zero rather than flooring, preserving the behaviour of
    # the USGS conversion this generalizes. The two differ only for a negative
    # sub-minute offset, which historical LMT zones do have inside the
    # supported range (Europe/Moscow LMT was +02:30:17); that the field cannot
    # represent those seconds at all is filed as #118.
    return local.date(), int(offset.total_seconds() / 60)


def _what_the_source_said(statement: SourceStatement) -> str:
    """Everything stated, in the source's own labels, on every path.

    Built from the statement rather than from whatever the resolving branch
    happened to have in scope, so a path cannot omit what it did not use. The
    days are the stated triples: a Julian day is never rendered as its
    Gregorian equivalent, because the source did not say that.
    """
    convention = statement.convention
    said: list[str] = []
    if statement.day is not None and convention is not None:
        meridian = (
            "a UTC calendar day"
            if convention.meridian is Meridian.UTC
            else "its local civil day"
        )
        calendar = (
            f" in the {convention.calendar.value.capitalize()} calendar"
            if convention.calendar is not CalendarSystem.GREGORIAN
            else ""
        )
        said.append(f"{statement.day} as {meridian}{calendar}")
    if statement.local_day is not None:
        calendar = (
            f" in the {convention.calendar.value.capitalize()} calendar"
            if convention is not None and convention.calendar is not CalendarSystem.GREGORIAN
            else ""
        )
        said.append(f"local civil day {statement.local_day}{calendar}")
    if statement.instant is not None:
        said.append(f"the instant {statement.instant.isoformat()}")
    return ", and ".join(said)


def _record(
    statement: SourceStatement,
    how: HowAssigned,
    profile_date: date,
    timezone_name: str | None,
    offset_minutes: int | None,
) -> ResolvedDay:
    """The one place a ResolvedDay is built.

    Every route through `resolve_day` ends here, so provenance cannot be
    bypassed by an early return and cannot omit an input the branch did not
    consult.
    """
    said = _what_the_source_said(statement)
    if how is HowAssigned.STATED_LOCAL_DAY:
        because = (
            f"Restated as {profile_date.isoformat()} on the Gregorian axis, "
            "which names the same local civil day under another calendar."
            if statement.convention is not None
            and statement.convention.calendar is not CalendarSystem.GREGORIAN
            else "Taken as reported and not re-derived."
        )
    elif how is HowAssigned.UTC_DAY_CONTAINED:
        because = (
            f"Every instant of that day falls on local "
            f"{profile_date.isoformat()} in {timezone_name}, so it denotes "
            "that day and nothing is invented by filing it there."
        )
    elif how is HowAssigned.UTC_DAY_RESOLVED_BY_LOCAL_STATEMENT:
        because = (
            f"The UTC day covers local {profile_date.isoformat()} in "
            f"{timezone_name}, and the local day the source stated is the one "
            "filed. The UTC day alone need not have denoted it."
        )
    else:
        because = (
            f"The day is derived from the instant under historical "
            f"{timezone_name} civil-time rules, not reported."
        )

    derived = how is HowAssigned.DERIVED_FROM_INSTANT
    return ResolvedDay(
        profile_date=profile_date,
        temporal_assignment=(
            TemporalAssignment.DIRECT_RECORD if derived else TemporalAssignment.REPORTED
        ),
        how_assigned=how,
        statement=statement,
        interpretation=f"The source states {said}. {because}",
        exact_timestamp=statement.instant,
        timezone_name=timezone_name if derived else None,
        utc_offset_minutes=offset_minutes if derived else None,
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
    cannot carry every day a source may legitimately state. `stated_local_day`
    is the source's own statement of the local civil day, in the same calendar.
    `instant` is an instant the source stated. Any may be given, or several.
    `timezone_name` is the IANA zone at the place of occurrence.

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

    statement = SourceStatement(
        day=None if stated_day is None else _as_civil(stated_day),
        convention=convention,
        local_day=None if stated_local_day is None else _as_civil(stated_local_day),
        instant=instant,
    )
    zone = _zone(timezone_name)

    if statement.day is None and statement.local_day is None:
        if statement.instant is None:
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
        profile_date, offset = _offset_minutes(statement.instant, zone, timezone_name)
        return _record(
            statement, HowAssigned.DERIVED_FROM_INSTANT, profile_date, timezone_name, offset
        )

    if statement.convention is None:
        raise UnresolvedDay(
            "The source's day convention is not established, so the stated day "
            "denotes nothing determinate and yields no date-specific event. A "
            "stated day carries no calendar of its own; the adapter declares "
            "one."
        )

    local_day = (
        None
        if statement.local_day is None
        else _on_gregorian_axis(statement.local_day, statement.convention.calendar)
    )

    if statement.day is None:
        # The source stated its local civil day and nothing needing
        # reconciliation against it, so it denotes itself.
        assert local_day is not None
        return _record(statement, HowAssigned.STATED_LOCAL_DAY, local_day, timezone_name, None)

    day = _on_gregorian_axis(statement.day, statement.convention.calendar)

    if statement.convention.meridian is not Meridian.UTC:
        if local_day is not None and local_day != day:
            raise UnresolvedDay(
                f"The source states local civil day {statement.local_day} and "
                f"also {statement.day} as its local civil day. It disagrees "
                "with itself and neither value resolves the other."
            )
        return _record(statement, HowAssigned.STATED_LOCAL_DAY, day, timezone_name, None)

    if zone is None or timezone_name is None:
        raise UnresolvedDay(
            f"The stated day {statement.day} is a UTC calendar day, and whether "
            "it denotes one local civil day depends on the place of occurrence, "
            "which is unknown."
        )

    touched = _local_dates_touched(day, zone)

    # A source stating its own local day is cross-checked against the UTC
    # interval whether or not that interval was ambiguous. A contradiction is
    # most dangerous exactly where the UTC day looks unambiguous, since nothing
    # else would surface it.
    if local_day is not None:
        if local_day not in touched:
            raise UnresolvedDay(
                f"The source states local civil day {statement.local_day}, but "
                f"no instant of its stated UTC calendar day {statement.day} "
                f"falls on that day in {timezone_name}. The source disagrees "
                "with itself and neither value resolves the other."
            )
        return _record(
            statement,
            HowAssigned.UTC_DAY_RESOLVED_BY_LOCAL_STATEMENT,
            local_day,
            timezone_name,
            None,
        )

    if touched == {day}:
        return _record(statement, HowAssigned.UTC_DAY_CONTAINED, day, timezone_name, None)

    # The day alone is ambiguous, but the contract admits other evidence that
    # resolves it -- an instant inside the interval lands on exactly one local
    # civil day. The day is then derived from that instant rather than
    # reported, because the source never stated the local day.
    if statement.instant is None:
        raise UnresolvedDay(
            f"The UTC calendar day {statement.day} falls across two local civil "
            f"days in {timezone_name}. Choosing one would invent precision the "
            "source never stated, so it yields no date-specific event without "
            "further evidence."
        )
    if statement.instant.astimezone(UTC).date() != day:
        raise UnresolvedDay(
            f"The stated instant {statement.instant.isoformat()} falls outside "
            f"the stated UTC calendar day {statement.day}, so the source "
            "disagrees with itself and neither value resolves the other."
        )
    profile_date, offset = _offset_minutes(statement.instant, zone, timezone_name)
    return _record(
        statement, HowAssigned.DERIVED_FROM_INSTANT, profile_date, timezone_name, offset
    )
