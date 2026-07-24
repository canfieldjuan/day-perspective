from pathlib import Path

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
    assert report.reviewed_count == 0
    assert report.published_count == 0
    assert report.release_ready is False
