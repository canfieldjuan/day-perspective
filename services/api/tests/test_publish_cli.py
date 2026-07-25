from __future__ import annotations

import sys
from contextlib import nullcontext
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import publish_cli
from app.models import PublicationManifest, PublicationStatus
from app.services import LocalFilesystemPublishedProfileStore, publish_day_profile
from tests.test_publication_atomicity import (
    PROFILE_DATE,
    PROFILE_TYPE,
    payload,
    statement_evidence,
)


@pytest.mark.integration
def test_reconcile_command_reports_and_repairs(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The recovery path publication errors point operators toward must be
    reachable through a supported command."""
    store = LocalFilesystemPublishedProfileStore(tmp_path)
    evidence = statement_evidence(session)
    publish_day_profile(
        session,
        store=store,
        profile_date=PROFILE_DATE,
        profile_type=PROFILE_TYPE,
        payload=payload("Published for CLI reconciliation."),
        statement_evidence=evidence,
    )
    orphan = tmp_path / "day" / date(1970, 1, 1).isoformat() / "profile-v1.json"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text('{"orphaned": true}', encoding="utf-8")

    class _Settings:
        published_profile_root = tmp_path

    monkeypatch.setattr(publish_cli, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(publish_cli, "get_settings", lambda: _Settings())

    monkeypatch.setattr(sys, "argv", ["publish_cli", "reconcile"])
    publish_cli.main()
    reported = capsys.readouterr().out
    assert "repair=false" in reported
    assert "orphan_artifacts=1" in reported
    assert orphan.exists(), "Report-only mode must not move artifacts."

    monkeypatch.setattr(sys, "argv", ["publish_cli", "reconcile", "--repair"])
    publish_cli.main()
    repaired = capsys.readouterr().out
    assert "repair=true" in repaired
    assert "orphan_artifacts=1" in repaired
    assert not orphan.exists()
    assert (tmp_path / "quarantine").exists()
    assert list(session.scalars(select(PublicationManifest.status))) == [
        PublicationStatus.PUBLISHED
    ]
