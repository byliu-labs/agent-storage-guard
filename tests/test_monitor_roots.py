"""Configuration must fail before starting scans or changing scheduled jobs."""
from pathlib import Path

import pytest

from agent_storage_guard.monitor_roots import defaults, validate


@pytest.mark.parametrize("field,value", [
    ("tick_interval_seconds", 0.5), ("scan_interval_seconds", True),
    ("scan_timeout_seconds", 3601), ("scan_total_timeout_seconds", 7201),
    ("warn_free_bytes", -1), ("urgent_cooldown_seconds", -1),
    ("alert_cooldown_seconds", "never"), ("recovery_margin_bytes", "2 GiB"),
])
def test_invalid_policy_is_rejected(field, value):
    config = defaults([])
    config[field] = value
    with pytest.raises(ValueError, match=field):
        validate(config)


def test_protected_application_roots_and_descendants_are_rejected():
    protected = Path.home() / "Library/Application Support/Nest"
    for root in [Path.home(), protected, protected / "data"]:
        with pytest.raises(ValueError, match="Nest"):
            validate({**defaults([]), "roots": [{"path": str(root)}]})


def test_grouping_validation_precedes_native_scan(tmp_path):
    for spec in [{"path": str(tmp_path), "prefix_groups": ["same-", "same-"]},
                 {"path": str(tmp_path), "prefix_groups": ["job-"], "depth": 2}]:
        with pytest.raises(ValueError):
            validate({**defaults([]), "roots": [spec]})
