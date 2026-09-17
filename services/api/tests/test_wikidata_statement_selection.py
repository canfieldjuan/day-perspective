"""Wikidata statement selection honours rank and snaktype (B1).

``_first`` used to read ``claims[property][0]`` -- the first statement in source
order, ignoring Wikidata's rank (a ``deprecated`` statement is a known-wrong
value that must never be read; a ``preferred`` one overrides ``normal``) and its
snaktype (``novalue``/``somevalue`` assert the *absence* of a value and carry no
datavalue to read). And the ingest quality check hardcoded
``unreferenced_fatality_candidate: True`` regardless of whether the fatality
statement actually had references. Both are latent on the canonical Q749610
fixture, whose statements are all single, normal-rank, value-snaktype, and whose
P1120 happens to be unreferenced -- so these tests exercise the shapes the
fixture cannot.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, QualityCheck, ReviewTask
from app.wikidata import (
    LocalFilesystemRawSourceStore,
    _first,
    _value,
    ingest_wikidata_candidate,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "data/fixtures/wikidata/Q749610.json"


def _quantity_statement(
    amount: str, *, rank: str = "normal", references: list[Any] | None = None
) -> dict[str, Any]:
    statement: dict[str, Any] = {
        "mainsnak": {
            "snaktype": "value",
            "property": "P1120",
            "datavalue": {"value": {"amount": amount, "unit": "1"}, "type": "quantity"},
            "datatype": "quantity",
        },
        "type": "statement",
        "rank": rank,
    }
    if references is not None:
        statement["references"] = references
    return statement


def _novalue_statement(*, rank: str = "normal") -> dict[str, Any]:
    return {
        "mainsnak": {
            "snaktype": "novalue",
            "property": "P1120",
            "datatype": "quantity",
        },
        "type": "statement",
        "rank": rank,
    }


def _entity(statements: list[dict[str, Any]]) -> dict[str, Any]:
    return {"claims": {"P1120": statements}}


def test_first_skips_a_deprecated_statement() -> None:
    entity = _entity(
        [
            _quantity_statement("+999999", rank="deprecated"),
            _quantity_statement("+139", rank="normal"),
        ]
    )
    assert _value(_first(entity, "P1120")) == {"amount": "+139", "unit": "1"}


def test_first_prefers_preferred_over_normal_regardless_of_order() -> None:
    entity = _entity(
        [
            _quantity_statement("+139", rank="normal"),
            _quantity_statement("+140", rank="preferred"),
        ]
    )
    assert _value(_first(entity, "P1120")) == {"amount": "+140", "unit": "1"}


def test_first_skips_a_novalue_mainsnak() -> None:
    entity = _entity(
        [
            _novalue_statement(),
            _quantity_statement("+139", rank="normal"),
        ]
    )
    assert _value(_first(entity, "P1120")) == {"amount": "+139", "unit": "1"}


def test_first_raises_when_no_statement_asserts_a_value() -> None:
    entity = _entity(
        [
            _quantity_statement("+1", rank="deprecated"),
            _novalue_statement(),
        ]
    )
    with pytest.raises(ValueError, match="no usable P1120"):
        _first(entity, "P1120")


def test_first_returns_the_only_normal_value_statement() -> None:
    entity = _entity([_quantity_statement("+139", rank="normal")])
    assert _value(_first(entity, "P1120")) == {"amount": "+139", "unit": "1"}


def _fixture_with_fatalities(
    tmp_path: Path, statements: list[dict[str, Any]]
) -> Path:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document["entities"]["Q749610"]["claims"]["P1120"] = statements
    path = tmp_path / "Q749610-fatalities.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _schema_check(session: Session) -> QualityCheck:
    check = session.scalar(
        select(QualityCheck).where(
            QualityCheck.check_name == "wikidata_q749610_schema",
            QualityCheck.subject_type == "source_release",
        )
    )
    assert check is not None
    return check


def test_ingest_selects_referenced_normal_fatalities_over_a_deprecated_value(
    session: Session, tmp_path: Path
) -> None:
    real = json.loads(FIXTURE.read_text(encoding="utf-8"))
    real_fatalities = real["entities"]["Q749610"]["claims"]["P1120"][0]
    referenced = copy.deepcopy(real_fatalities)
    referenced["references"] = [{"hash": "ref-1", "snaks": {}}]
    deprecated = copy.deepcopy(real_fatalities)
    deprecated["rank"] = "deprecated"
    deprecated["mainsnak"]["datavalue"]["value"]["amount"] = "+999999"
    fixture = _fixture_with_fatalities(tmp_path, [deprecated, referenced])

    ingest_wikidata_candidate(
        session,
        fixture_path=fixture,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )

    fatality = session.scalar(
        select(Claim).where(Claim.claim_type == "candidate_fatalities")
    )
    assert fatality is not None and fatality.assertion_json is not None
    # The deprecated value is never read; the referenced normal one is.
    assert fatality.assertion_json["value"] == {"amount": "+139", "unit": "1"}
    assert fatality.assertion_json["wikidata_reference_count"] == 1
    # The quality check reports the fatality's real reference state, not True.
    assert _schema_check(session).details["unreferenced_fatality_candidate"] is False
    # A referenced fatality is no longer flagged high-priority for review.
    task = session.scalar(
        select(ReviewTask).where(ReviewTask.claim_id == fatality.id)
    )
    assert task is not None and task.priority == "normal"


def test_ingest_reports_the_canonical_fixture_fatality_as_unreferenced(
    session: Session, tmp_path: Path
) -> None:
    ingest_wikidata_candidate(
        session,
        fixture_path=FIXTURE,
        raw_store=LocalFilesystemRawSourceStore(tmp_path / "raw"),
    )
    # The canonical Q749610 P1120 has no references, so the computed flag agrees
    # with the value the hardcoded True used to assert -- proving the fix keeps
    # the canonical outcome while deriving it from the data.
    assert _schema_check(session).details["unreferenced_fatality_candidate"] is True
    fatality = session.scalar(
        select(Claim).where(Claim.claim_type == "candidate_fatalities")
    )
    assert fatality is not None and fatality.assertion_json is not None
    assert fatality.assertion_json["wikidata_reference_count"] == 0
