"""Coordinates -> timezone via the PostGIS boundary table (A3).

The pure retrieval/parse guards run without a database; the seed + ST_Covers
lookup are exercised against PostGIS from a small committed fixture (the real
~170MB release is a pinned download, not something a test fetches).
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import TimezoneBoundary
from app.timezone_boundaries import (
    TimezoneDatasetError,
    _geojson_from_zip,
    _iter_timezone_features,
    _verify_checksum,
    seed_timezone_boundaries,
    timezone_for_coordinates,
)

MINI_FIXTURE = Path(__file__).parent / "fixtures" / "mini_timezones.geojson"


# --------------------------------------------------------------------------
# Retrieval and parsing (no database)
# --------------------------------------------------------------------------


class TestChecksumIsEnforced:
    def test_a_matching_checksum_is_accepted(self) -> None:
        payload = b"boundary bytes"
        _verify_checksum(payload, hashlib.sha256(payload).hexdigest())

    def test_a_mismatched_checksum_is_refused(self) -> None:
        with pytest.raises(TimezoneDatasetError, match="pinned checksum"):
            _verify_checksum(b"boundary bytes", "0" * 64)


class TestGeoJsonExtractedFromArchive:
    def _zip_with(self, entries: dict[str, str]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return buffer.getvalue()

    def test_the_single_geojson_entry_is_returned(self) -> None:
        payload = self._zip_with({"combined.json": '{"type":"FeatureCollection"}'})
        assert _geojson_from_zip(payload) == '{"type":"FeatureCollection"}'

    def test_an_archive_without_exactly_one_geojson_is_refused(self) -> None:
        with pytest.raises(TimezoneDatasetError, match="exactly one"):
            _geojson_from_zip(self._zip_with({"a.json": "{}", "b.json": "{}"}))
        with pytest.raises(TimezoneDatasetError, match="exactly one"):
            _geojson_from_zip(self._zip_with({"readme.txt": "x"}))


class TestFeaturesAreStreamed:
    def test_each_feature_yields_its_tzid_and_geometry(self) -> None:
        features = list(_iter_timezone_features(MINI_FIXTURE.read_text(encoding="utf-8")))
        tzids = [tzid for tzid, _ in features]
        assert tzids == ["America/Anchorage", "Europe/Berlin"]
        # The geometry is emitted as re-parseable JSON, never coordinates in Python.
        geometry_types = [json.loads(geometry)["type"] for _, geometry in features]
        assert geometry_types == ["Polygon", "MultiPolygon"]

    def test_a_non_feature_collection_is_refused(self) -> None:
        with pytest.raises(TimezoneDatasetError, match="FeatureCollection"):
            list(_iter_timezone_features('{"type":"Feature","geometry":{}}'))

    def test_a_feature_missing_its_tzid_is_refused(self) -> None:
        payload = '{"type":"FeatureCollection","features":[{"properties":{},"geometry":{}}]}'
        with pytest.raises(TimezoneDatasetError, match="tzid or geometry"):
            list(_iter_timezone_features(payload))


# --------------------------------------------------------------------------
# Seed + spatial lookup (PostGIS)
# --------------------------------------------------------------------------


def _seed_mini(session: Session) -> int:
    return seed_timezone_boundaries(
        session,
        url="",
        sha256="",
        dataset_version="mini-test",
        fixture_path=MINI_FIXTURE,
    )


@pytest.mark.integration
def test_a_covered_point_resolves_to_its_zone(session: Session, tmp_path: Path) -> None:
    _seed_mini(session)

    # The USGS golden epicenter (Prince William Sound) sits inside the Anchorage
    # polygon; A3b relies on exactly this resolving to America/Anchorage, and on
    # the resolution carrying the dataset version for provenance.
    anchorage = timezone_for_coordinates(session, latitude=60.9, longitude=-147.6)
    assert anchorage is not None
    assert anchorage.tzid == "America/Anchorage"
    assert anchorage.dataset_version == "mini-test"

    berlin = timezone_for_coordinates(session, latitude=50.5, longitude=10.5)
    assert berlin is not None
    assert berlin.tzid == "Europe/Berlin"


@pytest.mark.integration
def test_an_uncovered_point_resolves_to_none(session: Session, tmp_path: Path) -> None:
    _seed_mini(session)

    # Open ocean off West Africa: covered by no boundary, so the caller refuses
    # rather than assign a meridian (the contract's unknown-place rule).
    assert timezone_for_coordinates(session, latitude=0.0, longitude=0.0) is None


@pytest.mark.integration
def test_seeding_is_idempotent(session: Session, tmp_path: Path) -> None:
    first = _seed_mini(session)
    second = _seed_mini(session)

    assert first == second == 2
    assert session.scalar(select(func.count()).select_from(TimezoneBoundary)) == 2
    # A Polygon feature is stored as a single-part MultiPolygon, matching the
    # column, so every row is a valid MULTIPOLYGON.
    kinds = session.execute(
        text("SELECT DISTINCT ST_GeometryType(boundary_geometry) FROM timezone_boundaries")
    ).scalars().all()
    assert kinds == ["ST_MultiPolygon"]
