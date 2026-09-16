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


def _julian_to_gregorian(day: date) -> date:
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
    """Whether a UTC calendar day coincides with one local civil day.

    A UTC day is a twenty-four hour interval anchored elsewhere, so it need not
    line up with any local civil day. It does exactly when its first and last
    instants fall on the same local day, and that day is the one stated --
    which is a property of the date, not of the place: a location at zero
    offset in winter can be an hour off under summer time.
    """
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    last = start + timedelta(days=1) - timedelta(microseconds=1)
    return start.astimezone(zone).date() == day == last.astimezone(zone).date()


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
    stated_day: date | None = None,
    stated_day_end: date | None = None,
    convention: DayConvention | None = None,
    instant: datetime | None = None,
    timezone_name: str | None = None,
) -> ResolvedDay:
    """Resolve what a source stated to exactly one local civil day, or refuse.

    `stated_day` is a day the source stated, in `convention`. `instant` is an
    instant the source stated. Either may be given, or both. `timezone_name` is
    the IANA zone at the place of occurrence.

    Raises `UnresolvedDay` whenever the evidence does not denote exactly one
    local civil day, which is the contract's fail-closed default rather than an
    error condition.
    """
    if instant is not None and instant.tzinfo is None:
        raise UnresolvedDay(
            "A naive datetime does not denote an instant, so no local civil "
            "day follows from it."
        )

    if stated_day_end is not None and stated_day_end != stated_day:
        raise UnresolvedDay(
            f"The source states an interval ({stated_day} to {stated_day_end}), "
            "not a day. Which profile or profiles an occurrence interval "
            "belongs to is not decided by the contract; see issue #113. "
            "Refused rather than collapsed to the start date."
        )

    zone = _zone(timezone_name)

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

    restated: date | None = None
    day = stated_day
    if convention.calendar is CalendarSystem.JULIAN:
        restated = _julian_to_gregorian(stated_day)
        day = restated

    if convention.meridian is Meridian.UTC:
        if zone is None:
            raise UnresolvedDay(
                f"The stated day {day.isoformat()} is a UTC calendar day, and "
                "whether it denotes one local civil day depends on the place of "
                "occurrence, which is unknown."
            )
        if not _utc_day_denotes(day, zone):
            raise UnresolvedDay(
                f"The UTC calendar day {day.isoformat()} falls across two local "
                f"civil days in {timezone_name}. Choosing one would invent "
                "precision the source never stated, so it yields no "
                "date-specific event without further evidence."
            )

    if restated is not None:
        interpretation = (
            f"The source states {stated_day.isoformat()} in the Julian calendar, "
            f"restated as {day.isoformat()} on the Gregorian axis. The two name "
            "the same local civil day, so the day is reported, not derived."
        )
    else:
        interpretation = (
            f"The source states {day.isoformat()} as its local civil day. Taken "
            "as reported and not re-derived."
        )

    return ResolvedDay(
        profile_date=day,
        temporal_assignment=TemporalAssignment.REPORTED,
        exact_timestamp=instant,
        interpretation=interpretation,
        source_calendar=(
            convention.calendar if convention.calendar is not CalendarSystem.GREGORIAN else None
        ),
    )
