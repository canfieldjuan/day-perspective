from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text


@pytest.mark.integration
def test_migration_from_empty_state_creates_every_required_foundational_table(migrated_database: str) -> None:
    engine = create_engine(migrated_database)
    try:
        required = {
            "sources", "source_releases", "source_lineage", "claims", "resolved_claims", "events",
            "event_times", "geographies", "geography_versions", "event_locations", "people",
            "organizations", "entity_aliases", "external_identifiers", "metrics", "observations",
            "event_impacts", "metric_coverage", "quality_assessments", "methodologies", "derived_values",
            "publication_manifests", "publication_statement_evidence", "day_profiles", "pipeline_runs",
            "quality_checks", "review_tasks", "corrections",
        }
        assert required <= set(inspect(engine).get_table_names())
        statement_evidence_columns = {
            column["name"]
            for column in inspect(engine).get_columns("publication_statement_evidence")
        }
        assert {"evidence_snapshot", "evidence_snapshot_hash"} <= statement_evidence_columns
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT postgis_version()"))
    finally:
        engine.dispose()
