import json
from pathlib import Path

import pytest

from app.golden_set import validate_golden_set

ROOT = Path(__file__).resolve().parents[3]
GOLDEN_SET = ROOT / "data/golden-set/golden-dates-v1.json"


def test_golden_100_selection_has_required_coverage_but_is_not_falsely_green() -> None:
    report = validate_golden_set(GOLDEN_SET)

    assert report.record_count == 100
    assert report.profile_counts == {
        "limited_historical": 34,
        "standard_statistical": 33,
        "enhanced_structured": 33,
    }
    assert all(count > 0 for count in report.tag_counts.values())
    assert 0 <= report.reviewed_count <= report.record_count
    assert 0 <= report.published_count <= report.record_count
    assert report.release_ready is (
        report.reviewed_count == report.record_count
        and report.published_count == report.record_count
    )


@pytest.mark.parametrize(
    ("reviewed", "published", "ready"),
    [(0, 0, False), (4, 0, False), (100, 0, False), (0, 100, False), (100, 100, True)],
)
def test_selection_review_and_publication_are_independent(
    tmp_path: Path, reviewed: int, published: int, ready: bool
) -> None:
    selection = json.loads(GOLDEN_SET.read_text())
    for index, record in enumerate(selection["records"]):
        record["manual_review_status"] = (
            "reviewed" if index < reviewed else "pending_human_review"
        )
        record["publication_status"] = (
            "published_and_validated" if index < published else "not_generated"
        )
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(selection))

    report = validate_golden_set(path)

    assert report.reviewed_count == reviewed
    assert report.published_count == published
    assert report.release_ready is ready
