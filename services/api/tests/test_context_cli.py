from __future__ import annotations

import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import candidate_cli, context_cli, usgs_cli
from app.models import PipelineRun, QualityCheck


def test_context_cli_commits_failed_ingestion_audit_before_exiting(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = tmp_path / "invalid-wpp.csv"
    invalid.write_text("wrong,columns\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(context_cli, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(
        context_cli,
        "get_settings",
        lambda: SimpleNamespace(raw_source_root=tmp_path / "raw"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["context-cli", "ingest-un-wpp", "--fixture", str(invalid)],
    )

    with pytest.raises(ValueError):
        context_cli.main()

    session.expire_all()
    assert session.scalar(select(PipelineRun.status)) == "failed"
    assert session.scalar(select(QualityCheck.status)) == "failed"


def test_usgs_cli_commits_failed_ingestion_audit_before_exiting(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = tmp_path / "invalid-usgs.json"
    invalid.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    monkeypatch.setattr(usgs_cli, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(
        usgs_cli,
        "get_settings",
        lambda: SimpleNamespace(raw_source_root=tmp_path / "raw"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["usgs-cli", "ingest", "--fixture", str(invalid)],
    )

    with pytest.raises(ValueError):
        usgs_cli.main()

    session.expire_all()
    assert session.scalar(select(PipelineRun.status)) == "failed"
    assert session.scalar(select(QualityCheck.status)) == "failed"


def test_candidate_cli_commits_failed_ingestion_audit_before_exiting(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = tmp_path / "invalid-wikidata.json"
    invalid.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(candidate_cli, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(
        candidate_cli,
        "get_settings",
        lambda: SimpleNamespace(raw_source_root=tmp_path / "raw"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["candidate-cli", "ingest", "--fixture", str(invalid)],
    )

    with pytest.raises(ValueError):
        candidate_cli.main()

    session.expire_all()
    assert session.scalar(select(PipelineRun.status)) == "failed"
    assert session.scalar(select(QualityCheck.status)) == "failed"
