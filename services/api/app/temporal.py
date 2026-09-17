"""One shared temporal resolver, per docs/PRODUCT_CONTRACT.md and D049.

An event is filed under the conventional local civil day at its place of
occurrence. Every publisher of a date-specific event resolves its day here, so
the same instant and place cannot reach different profiles depending on which
adapter ingested them.

The contract states a test rather than a list of conventions: a stated day
yields a date-specific event only where that convention, applied at the place
of occurrence, makes the day denote exactly one local civil day. This module is
that test.

It implements the test for the conventions publishers in this tree actually
state, and refuses the rest by name rather than mishandling them:

- a stated local civil day, which denotes itself (Wikidata);
- an instant, from which a day is derived (USGS);
- a stated UTC calendar day, refused, deferred to #120;
- a Julian civil date, refused, deferred to #114;
- a multi-day occurrence interval, refused per D050 (endpoint-resolution
  enforcement in A2c).

The refusals are the point of the vocabulary. `CalendarSystem` and `Meridian`
name conventions this module declines, so an adapter whose source uses one says
so and is refused, rather than being left with no way to state it truthfully.
A convention is never presumed and a refusal is never silent.

Provenance is built the same way. What the source stated is captured once,
whole, as a `SourceStatement`; every path resolves to a `HowAssigned` and the
record is constructed in exactly one place from that pair. No path can return
early past provenance construction, and no combination of inputs can reach a
branch that does not know about it. An earlier revision assembled the text at
the end from scattered locals, and consecutive review rounds each found a
provenance defect, several introduced by the previous round's fix.

The same discipline governs the inputs: a `DayConvention` describes a stated
day, so one supplied without a stated day describes nothing and is refused
rather than retained and later published. Publishing a convention for a day the
source never stated was the last defect of that series (#119, round 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import TemporalAssignment


class CalendarSystem(str, Enum):
    """The calendar system that names a day.

    `JULIAN` exists to be declared and refused: a source using it must be able
    to say so. Restating a Julian date on the Gregorian axis is #114, which
    arrives with Wikidata's `calendarmodel` and a real fixture.
    """

    GREGORIAN = "gregorian"
    JULIAN = "julian"


class Meridian(str, Enum):
    """The meridian at which a day begins.

    `UTC` exists to be declared and refused, for the same reason. Whether a UTC
    calendar day denotes exactly one local civil day is an interval question,
    deferred to #120.
    """

    LOCAL_CIVIL = "local_civil"
    UTC = "utc"


@dataclass(frozen=True)
class DayConvention:
    """How a source states its days.

    A convention fixes both things independently; neither is presumed. An
    adapter that cannot establish both has not established the convention, and
    a day whose convention is not established denotes nothing determinate.
    """

    calendar: CalendarSystem
    meridian: Meridian


@dataclass(frozen=True)
class SourceStatement:
    """Everything the source said about when the event happened, unconverted.

    Held whole and separate from anything derived from it, so the published
    record can state what was said without a conversion standing in for it.
    """

    day: date | None = None
    convention: DayConvention | None = None
    instant: datetime | None = None


class HowAssigned(str, Enum):
    """The route from a `SourceStatement` to one local civil day.

    Exhaustive by construction: every successful resolution is exactly one of
    these, and the record is rendered from the pair.
    """

    STATED_LOCAL_DAY = "stated_local_day"
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


class UnresolvedDay(ValueError):
    """The evidence does not denote exactly one local civil day.

    Raised rather than returning a best guess: the product refuses a
    date-specific event instead of inventing precision a source never stated.
    A refusal is a normal fail-closed outcome here, not a defect.

    A `ValueError` so that callers already treating an unusable source value as
    a validation failure keep working unchanged; `except UnresolvedDay` still
    distinguishes a contract refusal from an ordinary bad value.
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


def _offset_minutes(
    instant: datetime, zone: ZoneInfo, timezone_name: str
) -> tuple[date, int]:
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
    """Everything stated, in the source's own terms, on every path.

    Built from the statement rather than from whatever the resolving branch
    happened to have in scope, so a path cannot omit what it did not use.
    """
    said: list[str] = []
    if statement.day is not None:
        said.append(f"{statement.day.isoformat()} as its local civil day")
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
    derived = how is HowAssigned.DERIVED_FROM_INSTANT
    because = (
        f"The day is derived from the instant under historical {timezone_name} "
        "civil-time rules, not reported."
        if derived
        else "Taken as reported and not re-derived."
    )
    return ResolvedDay(
        profile_date=profile_date,
        temporal_assignment=(
            TemporalAssignment.DIRECT_RECORD if derived else TemporalAssignment.REPORTED
        ),
        how_assigned=how,
        statement=statement,
        interpretation=f"The source states {_what_the_source_said(statement)}. {because}",
        exact_timestamp=statement.instant,
        timezone_name=timezone_name if derived else None,
        utc_offset_minutes=offset_minutes if derived else None,
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

    `stated_day` is a day the source stated, in `convention`; the two go
    together, because a day whose convention is not established denotes nothing
    determinate and a convention without a day describes nothing. `instant` is
    an instant the source stated. `timezone_name` is the IANA zone at the place
    of occurrence, required to derive a day from an instant.

    A source may state both a day and an instant. The day is then reported and
    the instant preserved as stated, neither re-derived nor discarded.

    Raises `UnresolvedDay` whenever the evidence does not denote exactly one
    local civil day, which is the contract's fail-closed default rather than an
    error condition.
    """
    # Python's definition of naive, not just a missing tzinfo: a tzinfo whose
    # utcoffset() returns None leaves the value naive, and astimezone() would
    # then read it in the host process's local zone -- so the same source data
    # would publish a different day depending on which machine ingested it.
    if instant is not None and (
        instant.tzinfo is None or instant.utcoffset() is None
    ):
        raise UnresolvedDay(
            "A naive datetime does not denote an instant, so no local civil "
            "day follows from it. A tzinfo whose utcoffset() is None leaves "
            "the value naive."
        )
    if stated_day_end is not None and stated_day_end != stated_day:
        raise UnresolvedDay(
            f"The source states an interval ({stated_day} to {stated_day_end}), "
            "not a day. A multi-day interval yields no date-specific event "
            "(D050): it is refused rather than collapsed to its start date."
        )

    statement = SourceStatement(day=stated_day, convention=convention, instant=instant)
    zone = _zone(timezone_name)

    if statement.day is None:
        if statement.convention is not None:
            raise UnresolvedDay(
                "A day convention describes a stated day, and the source "
                "stated none. Recording it would publish a convention for a "
                "day that was never stated."
            )
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
            statement,
            HowAssigned.DERIVED_FROM_INSTANT,
            profile_date,
            timezone_name,
            offset,
        )

    if statement.convention is None:
        raise UnresolvedDay(
            "The source's day convention is not established, so the stated day "
            "denotes nothing determinate and yields no date-specific event. A "
            "stated day carries no calendar of its own; the adapter declares "
            "one."
        )
    if statement.convention.calendar is not CalendarSystem.GREGORIAN:
        raise UnresolvedDay(
            f"The source states its days in the "
            f"{statement.convention.calendar.value.capitalize()} calendar. "
            "Restating a non-Gregorian day on the product's Gregorian axis is "
            "not implemented; see issue #114. Refused rather than read as a "
            "Gregorian date, which would name a different day."
        )
    if statement.convention.meridian is not Meridian.LOCAL_CIVIL:
        raise UnresolvedDay(
            f"The stated day {stated_day} is a UTC calendar day. Whether such a "
            "day denotes exactly one local civil day depends on the place of "
            "occurrence and is not implemented; see issue #120. Refused rather "
            "than read as a local civil day, which it need not be."
        )

    # A day already stated as the conventional local civil day denotes itself,
    # whether or not an instant accompanies it. The instant is preserved as
    # stated rather than re-deriving a day the source already gave.
    return _record(
        statement, HowAssigned.STATED_LOCAL_DAY, statement.day, timezone_name, None
    )
