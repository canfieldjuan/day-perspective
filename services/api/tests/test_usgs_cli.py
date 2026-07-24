from __future__ import annotations

import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import usgs_cli
from app.models import PipelineRun, QualityCheck


def test_usgs_cli_commits_failed_ingestion_audit_before_exiting(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = tmp_path / "invalid-usgs.json"
    invalid.write_text(
        '{"type":"FeatureCollection","features":[]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(usgs_cli, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(
        usgs_cli,
        "get_settings",
        lambda: SimpleNamespace(
            raw_source_root=tmp_path / "raw",
            published_profile_root=tmp_path / "published",
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["usgs-cli", "ingest", "--fixture", str(invalid)],
    )

    with pytest.raises(ValueError, match="exactly one"):
        usgs_cli.main()

    session.expire_all()
    assert session.scalar(select(PipelineRun.status)) == "failed"
    assert session.scalar(select(QualityCheck.status)) == "failed"
