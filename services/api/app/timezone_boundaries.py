"""Coordinates -> timezone via a PostGIS boundary table (A3).

An event whose place of occurrence is unknown yields no day, and a day derived
from an instant needs the timezone at its place (docs/PRODUCT_CONTRACT.md).
A2 took an explicit timezone; this supplies the missing mechanism: the IANA
timezone whose boundary covers a point, resolved with PostGIS ``ST_Covers`` over
a table seeded from a pinned timezone-boundary-builder release.

The seed verifies the download against a pinned SHA256 and records the release
version, so the boundary data a day is derived under is a specific, reproducible
dataset (D013). The full release is ~170MB of GeoJSON, so it is streamed rather
than loaded whole, and each geometry is handed to PostGIS as text without ever
being parsed into Python.
"""

from __future__ import annotations

import hashlib
import io
import json
import urllib.request
import zipfile
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session


class TimezoneDatasetError(RuntimeError):
    """The timezone boundary dataset could not be retrieved, verified, or parsed."""


def _verify_checksum(payload: bytes, expected_sha256: str) -> None:
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected_sha256:
        raise TimezoneDatasetError(
            "Timezone boundary download did not match its pinned checksum "
            f"(expected {expected_sha256}, got {actual})."
        )


def _geojson_from_zip(payload: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.endswith(".json") or name.endswith(".geojson")
        ]
        if len(names) != 1:
            raise TimezoneDatasetError(
                "The timezone boundary archive must contain exactly one GeoJSON "
                f"file; found {archive.namelist()}."
            )
        return archive.read(names[0]).decode("utf-8")


def _iter_timezone_features(geojson_text: str) -> Iterator[tuple[str, str]]:
    """Yield ``(tzid, geometry_json)`` for each feature, one at a time.

    Streams the FeatureCollection with ``raw_decode`` rather than ``json.loads``
    so the ~170MB release is never fully materialized as Python objects: only the
    raw text plus one feature at a time is held. The geometry is re-serialized to
    text and handed to ``ST_GeomFromGeoJSON``, so its coordinates are parsed by
    PostGIS, never in Python.
    """
    try:
        features_at = geojson_text.index('"features"')
        start = geojson_text.index("[", features_at)
    except ValueError as error:
        raise TimezoneDatasetError(
            "The timezone dataset is not a GeoJSON FeatureCollection."
        ) from error
    decoder = json.JSONDecoder()
    index = start + 1
    length = len(geojson_text)
    while True:
        while index < length and geojson_text[index] in " \t\r\n,":
            index += 1
        if index >= length or geojson_text[index] == "]":
            break
        feature, index = decoder.raw_decode(geojson_text, index)
        properties = feature.get("properties") or {}
        tzid = properties.get("tzid")
        geometry = feature.get("geometry")
        if not isinstance(tzid, str) or geometry is None:
            raise TimezoneDatasetError(
                "A timezone feature is missing its tzid or geometry."
            )
        yield tzid, json.dumps(geometry)


def load_timezone_boundaries(
    session: Session, *, geojson_text: str, dataset_version: str
) -> int:
    """Replace ``timezone_boundaries`` from a GeoJSON FeatureCollection.

    Idempotent: existing rows are cleared first, so re-seeding yields the same
    table rather than duplicates. Each feature's geometry is normalized to
    MULTIPOLYGON at SRID 4326 (a Polygon feature becomes a single-part
    MultiPolygon), matching the column and the ``ST_Covers`` lookup.
    """
    session.execute(text("DELETE FROM timezone_boundaries"))
    insert = text(
        "INSERT INTO timezone_boundaries "
        "(id, tzid, dataset_version, boundary_geometry) VALUES "
        "(:id, :tzid, :dataset_version, "
        "ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326)))"
    )
    count = 0
    for tzid, geometry in _iter_timezone_features(geojson_text):
        session.execute(
            insert,
            {
                "id": uuid4(),
                "tzid": tzid,
                "dataset_version": dataset_version,
                "geometry": geometry,
            },
        )
        count += 1
    if count == 0:
        raise TimezoneDatasetError("The timezone dataset contained no features.")
    return count


def _download(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "day-perspective-offline-ingestion/0.1"}
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        payload: bytes = response.read()
    return payload


def seed_timezone_boundaries(
    session: Session,
    *,
    url: str,
    sha256: str,
    dataset_version: str,
    fixture_path: Path | None = None,
) -> int:
    """Seed the ``timezone_boundaries`` table and return the row count.

    From a local GeoJSON when ``fixture_path`` is given (tests and a smaller
    development set); otherwise download the pinned release, verify it against
    ``sha256``, and read its single GeoJSON entry. The download is refused unless
    it matches the pinned checksum, so the boundary data a day is later derived
    under is exactly the release recorded in ``dataset_version`` (D013).
    """
    if fixture_path is not None:
        geojson_text = fixture_path.read_text(encoding="utf-8")
    else:
        payload = _download(url)
        _verify_checksum(payload, sha256)
        geojson_text = _geojson_from_zip(payload)
    return load_timezone_boundaries(
        session, geojson_text=geojson_text, dataset_version=dataset_version
    )


def timezone_for_coordinates(
    session: Session, *, latitude: float, longitude: float
) -> str | None:
    """The IANA timezone whose boundary covers the point, or ``None``.

    ``None`` when no boundary covers it -- an open-ocean or otherwise unlocated
    point -- so a caller deriving a day from an instant refuses rather than
    assign a meridian the evidence does not support (the contract's "an instant
    whose place of occurrence is unknown yields no date-specific event").
    ``ST_Covers`` (not ``ST_Contains``) so a point exactly on a boundary still
    resolves; ``tzid`` order breaks the rare two-zone overlap deterministically.
    """
    row = session.execute(
        text(
            "SELECT tzid FROM timezone_boundaries "
            "WHERE ST_Covers(boundary_geometry, "
            "ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)) "
            "ORDER BY tzid LIMIT 1"
        ),
        {"latitude": latitude, "longitude": longitude},
    ).first()
    return None if row is None else str(row[0])
