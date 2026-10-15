"""Reports help review current evidence without counting stale Git refs as bytes."""
from agent_storage_guard.workstation_report import render


def test_live_worktrees_precede_stale_registration_noise():
    result = render({"worktrees": {"worktrees": [
        *[{"path": f"/old/retired-{index}", "prunable": "gitdir points to a missing location"} for index in range(70)],
        {"path": "/dev/unfinished-project", "branch": "feature"},
    ]}})
    assert "/dev/unfinished-project" in result
    assert "/old/retired" not in result
    assert "70 stale registrations (not evidence of disk usage)" in result


def test_missing_memory_evidence_is_not_zero_or_healthy():
    result = render({"capacity": {"memory": {"supported": True, "pressure": "unknown", "error": "access unavailable"}}})
    assert "Swap: 0" not in result
    assert "access unavailable" in result
    assert "no healthy state is inferred" in result
