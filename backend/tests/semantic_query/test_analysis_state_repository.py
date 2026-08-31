from app.agents.active_analysis_state import ActiveAnalysisState
from app.agents.analysis_state_repository import InMemoryAnalysisStateRepository


def _state(version: int = 1) -> ActiveAnalysisState:
    return ActiveAnalysisState(
        session_id="s1",
        user_id=7,
        space_id="travel",
        last_analysis_spec_id="spec_1",
        measures=[{"source_field": "order_id", "aggregation": "count"}],
        version=version,
    )


def test_state_save_rejects_stale_expected_version():
    repo = InMemoryAnalysisStateRepository()
    assert repo.save(_state(), expected_version=0)["version"] == 1

    updated = _state(version=1)
    assert repo.save(updated, expected_version=1)["version"] == 2
    assert repo.save(updated, expected_version=1) == {"ok": False, "error": "state_version_conflict"}
