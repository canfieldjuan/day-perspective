from __future__ import annotations

import hashlib
import json

from app.fixtures import _fixture_path, _raw_fixture_path


def test_test_fixture_raw_artifact_matches_its_declared_checksum() -> None:
    payload = json.loads(_fixture_path().read_text(encoding="utf-8"))
    release = payload["release"]
    artifact = _raw_fixture_path(release["raw_storage_uri"])
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == release["raw_checksum_sha256"]
