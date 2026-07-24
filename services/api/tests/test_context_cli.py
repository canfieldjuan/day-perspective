from __future__ import annotations

import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import context_cli
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
