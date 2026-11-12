"""CLI subject propagation, without a database or outbound requests."""

import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app import candidate_cli as cli


@pytest.mark.parametrize(
    ("handler", "service"),
    [
        ("_enrich", "attempt_wikidata_enrichment"),
        ("_resolve", "resolve_wikidata_event"),
        ("_publish", "publish_wikidata_event"),
        ("_adjudicate", "resolve_merge_review"),
    ],
)
def test_each_cli_stage_forwards_the_requested_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, handler: str, service: str
) -> None:
    requested = uuid4()
    received: list[Any] = []

    def run(session: Any, **kwargs: Any) -> Any:
        received.append(kwargs.get("source_release_id"))
        if handler == "_adjudicate":
            return ()
        return SimpleNamespace(
            status="published",
            occurrence_date="1964-03-27",
            colliding_manifest_id=None,
            manifest_id=None,
            merge_review_task_id=None,
            id=uuid4(),
            event_type="Q7944",
            canonical_title="SYNTHETIC test event",
        )

    monkeypatch.setattr(cli, service, run)
    args = SimpleNamespace(
        release=requested,
        force_new_version=False,
        decision="distinct_event",
        reviewer="test-human",
        rationale="Test decision",
        survivor_event_id=None,
    )
    getattr(cli, handler)(args, SimpleNamespace(published_profile_root=tmp_path), object())

    assert received == [requested]


@pytest.mark.parametrize("stage", ("enrich", "resolve", "publish", "adjudicate"))
def test_release_option_is_parsed_as_uuid(monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    requested = uuid4()
    received: list[Any] = []
    session = SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    def handle(args: Any, settings: Any, current: Any) -> str:
        assert current is session
        received.append(args.release)
        return "test-only"

    monkeypatch.setattr(cli, "_" + stage, handle)
    monkeypatch.setattr(cli, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(cli, "get_settings", lambda: None)
    argv = ["candidate_cli", stage, "--release", str(requested)]
    if stage == "adjudicate":
        argv += [
            "--decision",
            "distinct_event",
            "--reviewer",
            "test-human",
            "--rationale",
            "Offline test",
        ]
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    assert received == [requested]


@pytest.mark.parametrize("value", ("", "0", "False", "not-a-uuid"))
def test_invalid_release_is_refused_before_opening_a_session(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    def unexpected_session() -> Any:
        pytest.fail("An invalid release must not reach a database session")

    monkeypatch.setattr(cli, "SessionLocal", unexpected_session)
    monkeypatch.setattr(sys, "argv", ["candidate_cli", "resolve", "--release", value])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
