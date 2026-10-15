"""Reports explain evidence without inventing attribution or cleanup authority."""
from copy import deepcopy

from agent_storage_guard.report_insights import build

GIB = 1024 ** 3
NOW = 2_000_000.0


def payload():
    return {"time": NOW, "config": {"warn_free_bytes": 50 * GIB,
            "roots": [{"path": "/home/test/projects"}, {"path": "/tmp"}]},
            "capacity": {"time": NOW, "free_bytes": 32 * GIB,
                         "memory": {"pressure": "normal", "swap_used_bytes": 10 * GIB}},
            "scan": {"time": NOW, "groups": {
                "/home/test/projects": {"complete": True, "bytes": 20 * GIB},
                "/home/test/projects/app": {"complete": True, "bytes": 12 * GIB},
                "/tmp": {"complete": True, "bytes": 30 * GIB},
                "/tmp/[test-*]": {"complete": True, "bytes": 25 * GIB}},
                "baseline": {}}, "folder_history": [{"time": NOW - 86400, "groups": {
                "/home/test/projects": {"complete": True, "bytes": 18 * GIB},
                "/home/test/projects/app": {"complete": True, "bytes": 10 * GIB},
                "/tmp": {"complete": True, "bytes": 24 * GIB},
                "/tmp/[test-*]": {"complete": True, "bytes": 19 * GIB}}}],
            "capacity_history": [{"time": NOW - 86400, "free_bytes": 40 * GIB}]}


def test_current_status_distinguishes_free_space_and_swap():
    view = build(payload())
    assert view["status"]["shortfall_bytes"] == 18 * GIB
    assert view["status"]["severity"] == "warning"
    assert view["status"]["memory"]["pressure"] == "normal"
    assert not any("Restart" in action["title"] for action in view["actions"])


def test_high_level_locations_do_not_sum_parent_and_child():
    view = build(payload())
    assert sum(row["bytes"] for row in view["locations"]) == 50 * GIB
    assert sum(row["delta_bytes"] for row in view["locations"]) == 8 * GIB
    assert view["period"]["label"] == "Past day"
    assert view["period"]["free_delta_bytes"] == -8 * GIB


def test_action_names_actual_grower_without_authorizing_disposal():
    view = build(payload())
    action = next(item for item in view["actions"] if item.get("path") == "/tmp/[test-*]")
    assert "6.0 GiB" in action["body"]
    assert "unfinished" in action["body"]
    assert "Trash" not in action["body"]


def test_unknown_root_is_not_zero_and_older_complete_is_labeled():
    data = payload()
    data["scan"]["groups"]["/tmp"] = {"complete": False, "error": "permission denied"}
    del data["scan"]["groups"]["/tmp/[test-*]"]
    view = build(data)
    row = next(item for item in view["locations"] if item["label"] == "Temporary work")
    assert row["coverage"] == "older"
    assert row["bytes"] == 24 * GIB
    assert row["delta_bytes"] is None
    assert row["observed_at"] == NOW - 86400
    assert view["appendix"]["coverage_gaps"][0]["path"] == "/tmp"


def test_missing_history_never_fabricates_a_change():
    data = payload()
    data["folder_history"] = []
    data["capacity_history"] = []
    view = build(data)
    assert view["period"]["label"] == "Building a baseline"
    assert all(item["delta_bytes"] is None for item in view["locations"])


def test_nested_configured_roots_do_not_double_count():
    data = payload()
    data["config"]["roots"].append({"path": "/home/test/projects/app"})
    view = build(data)
    assert sum(row["bytes"] for row in view["locations"]) == 50 * GIB


def test_stale_capacity_is_not_reported_as_a_current_problem():
    data = payload()
    data["time"] += 86400
    view = build(data)
    assert view["status"]["severity"] == "unknown"
    assert not view["status"]["capacity_fresh"]
    assert view["actions"][0]["title"] == "Refresh the measurements"


def test_pressure_requires_concrete_save_and_app_action():
    data = payload()
    data["capacity"]["memory"]["pressure"] = "critical"
    view = build(data)
    action = view["actions"][0]
    assert "Save" in action["title"]
    assert "Activity Monitor" in action["body"]
    assert "swap files" in action["body"]


def test_week_is_selected_when_it_explains_much_more_loss_than_day():
    data = payload()
    data["capacity_history"].append({"time": NOW - 6 * 86400, "free_bytes": 65 * GIB})
    data["folder_history"].append({"time": NOW - 6 * 86400, "groups": {
        "/tmp": {"complete": True, "bytes": 3 * GIB}}})
    view = build(data)
    assert view["period"]["label"] == "Past week"
    assert view["period"]["elapsed_seconds"] == 6 * 86400
    assert view["period"]["free_delta_bytes"] == -33 * GIB


def test_period_elapsed_uses_actual_samples_not_nominal_day():
    data = payload()
    data["capacity_history"][0]["time"] -= 1800
    data["folder_history"][0]["time"] -= 1800
    view = build(data)
    assert view["period"]["elapsed_seconds"] == 88200
    row = next(item for item in view["locations"] if item["label"] == "Temporary work")
    assert row["period_elapsed_seconds"] == 88200


def test_build_is_pure_and_json_serializable():
    import json
    data = payload()
    original = deepcopy(data)
    json.dumps(build(data), allow_nan=False)
    assert data == original


def test_partial_subfolder_is_not_used_to_claim_a_top_grower():
    data = payload()
    data["scan"]["groups"]["/tmp/[test-*]"]["complete"] = False
    view = build(data)
    assert not any(row["path"] == "/tmp/[test-*]" for row in view["changes"]["rows"])
    older = next(a for a in view["actions"] if a.get("path") == "/tmp/[test-*]")
    assert "Current size is unknown" in older["body"]


def test_new_child_only_compares_from_zero_when_parent_listing_was_complete():
    data = payload()
    groups = data["folder_history"][0]["groups"]
    del groups["/home/test/projects/app"]
    groups["/home/test/projects"]["children_complete"] = True
    view = build(data)
    row = next(item for item in view["appendix"]["folders"] if item["path"] == "/home/test/projects/app")
    assert row["delta_bytes"] == 12 * GIB
    groups["/home/test/projects"]["children_complete"] = False
    view = build(data)
    row = next(item for item in view["appendix"]["folders"] if item["path"] == "/home/test/projects/app")
    assert row["delta_bytes"] is None


def test_per_folder_observation_times_are_not_replaced_by_scan_completion():
    data = payload()
    data["scan"]["groups"]["/tmp"]["observed_at"] = NOW - 120
    data["folder_history"][0]["groups"]["/tmp"]["observed_at"] = NOW - 86400 - 60
    view = build(data)
    row = next(item for item in view["locations"] if item["label"] == "Temporary work")
    assert row["observed_at"] == NOW - 120
    assert row["period_elapsed_seconds"] == 86340


def test_partial_without_older_complete_value_stays_unknown():
    data = payload()
    data["scan"]["groups"]["/tmp"] = {"complete": False, "bytes": 3 * GIB}
    data["folder_history"][0]["groups"]["/tmp"]["complete"] = False
    del data["scan"]["groups"]["/tmp/[test-*]"]
    del data["folder_history"][0]["groups"]["/tmp/[test-*]"]
    view = build(data)
    row = next(item for item in view["locations"] if item["label"] == "Temporary work")
    assert row["coverage"] == "unknown"
    assert row["bytes"] is None
    assert row["delta_bytes"] is None


def test_hour_is_used_when_day_and_week_have_no_comparable_samples():
    data = payload()
    data["capacity_history"][0]["time"] = NOW - 3700
    data["folder_history"][0]["time"] = NOW - 3700
    view = build(data)
    assert view["period"]["label"] == "Past hours"
    assert view["period"]["elapsed_seconds"] == 3700


def test_missing_memory_measurement_is_explicitly_unknown():
    data = payload()
    data["capacity"]["memory"] = {"pressure": "unknown", "error": "unsupported"}
    data["capacity"]["free_bytes"] = 60 * GIB
    view = build(data)
    assert view["status"]["memory"]["pressure"] == "unknown"
    assert view["status"]["memory"]["error"] == "unsupported"
    assert not any("restart" in action["title"].lower() for action in view["actions"])


def test_short_capacity_history_does_not_hide_day_folder_changes():
    data = payload()
    data["capacity_history"] = [{"time": NOW - 17 * 60, "free_bytes": 33 * GIB}]
    view = build(data)
    assert view["period"]["label"] == "Past day"
    assert view["period"]["free_delta_bytes"] is None
    assert view["changes"]["rows"][0]["delta_bytes"] == 6 * GIB
    assert "comparable disk-capacity history is not available" in view["changes"]["summary"]


def test_partial_root_shows_current_nonoverlapping_subgroups_without_old_total():
    data = payload()
    data["scan"]["groups"]["/tmp"]["complete"] = False
    data["scan"]["groups"]["/tmp/nested"] = {"complete": True, "bytes": 4 * GIB}
    data["scan"]["groups"]["/tmp/nested/child"] = {"complete": True, "bytes": 3 * GIB}
    view = build(data)
    row = next(item for item in view["locations"] if item["label"] == "Temporary work")
    assert row["coverage"] == "partial"
    assert row["bytes"] is None
    assert row["known_bytes"] == 29 * GIB
    assert row["known_delta_bytes"] == 6 * GIB
    assert row["delta_bytes"] is None


def test_tiny_descendant_cannot_hide_large_parent_growth():
    data = payload()
    data["scan"]["groups"]["/tmp"]["bytes"] = 60 * GIB
    data["scan"]["groups"]["/tmp/[test-*]"]["bytes"] = 20 * GIB
    view = build(data)
    assert view["changes"]["rows"][0]["path"] == "/tmp"
    assert view["changes"]["rows"][0]["delta_bytes"] == 36 * GIB


def test_growing_project_is_shown_when_projects_overall_shrink():
    data = payload()
    data["scan"]["groups"]["/home/test/projects"]["bytes"] = 15 * GIB
    view = build(data)
    assert any(row["path"] == "/home/test/projects/app" for row in view["changes"]["rows"])


def test_stale_top_growers_are_withheld_but_appendix_preserves_evidence():
    data = payload()
    data["time"] += 86400
    view = build(data)
    assert view["changes"]["rows"] == []
    assert view["appendix"]["folders"]


def test_configured_prefix_groups_provide_real_inspection_path():
    data = payload()
    data["config"]["roots"][1]["prefix_groups"] = ["test-"]
    view = build(data)
    row = next(item for item in view["appendix"]["folders"] if item["path"] == "/tmp/[test-*]")
    assert row["grouped"]
    assert row["inspect_path"] == "/tmp"


def test_failed_latest_root_retains_prior_child_as_older_not_current():
    data = payload()
    data["scan"]["groups"] = {"/tmp": {"complete": False}}
    data["folder_history"][0]["groups"]["/tmp"]["complete"] = False
    view = build(data)
    row = next(r for r in view["appendix"]["folders"] if r["path"] == "/tmp/[test-*]")
    assert row["coverage"] == "older"
    assert row["bytes"] == 19 * GIB
    assert row["delta_bytes"] is None
    area = next(r for r in view["locations"] if r["label"] == "Temporary work")
    assert area["coverage"] == "older_partial"
    assert area["known_bytes"] == 0
    assert area["older_known_bytes"] == 19 * GIB
    action = next(r for r in view["actions"] if r.get("path") == "/tmp/[test-*]")
    assert "Current size is unknown" in action["body"]


def test_complete_current_parent_does_not_resurrect_absent_old_children():
    data = payload()
    del data["scan"]["groups"]["/tmp/[test-*]"]
    data["scan"]["groups"]["/tmp"]["children_complete"] = True
    view = build(data)
    assert not any(r["path"] == "/tmp/[test-*]" for r in view["appendix"]["folders"])


def test_headline_names_disk_problem_without_implying_memory_problem():
    view = build(payload())
    assert view["status"]["headline"] == "Disk space is below your reserve"


def test_headline_names_memory_only_and_both_conditions():
    data = payload()
    data["capacity"]["memory"]["pressure"] = "critical"
    assert build(data)["status"]["headline"] == "Disk space is low and memory pressure is high"
    data["capacity"]["free_bytes"] = 60 * GIB
    assert build(data)["status"]["headline"] == "Memory pressure is critical"


def test_unknown_memory_never_gets_a_healthy_memory_headline():
    data = payload()
    data["capacity"]["free_bytes"] = 60 * GIB
    data["capacity"]["memory"] = {"pressure": "unknown"}
    assert build(data)["status"]["headline"] == "Disk space is within your reserve"


def test_recent_capacity_is_independent_from_folder_day_period():
    data = payload()
    data["capacity_history"] = [{"time": NOW - 17 * 60, "free_bytes": 33 * GIB}]
    view = build(data)
    assert view["period"]["label"] == "Past day"
    assert view["period"]["free_delta_bytes"] is None
    assert view["changes"]["capacity_recent"] == {"delta_bytes": -GIB,
        "elapsed_seconds": 17 * 60, "started_at": NOW - 17 * 60, "ended_at": NOW}


def test_recent_capacity_filters_old_or_different_volume_observations():
    data = payload()
    data["capacity"]["volume_path"] = "/disk-a"
    data["capacity_history"] = [
        {"time": NOW - 3600, "free_bytes": 35 * GIB, "volume_path": "/disk-b"},
        {"time": NOW - 40 * 3600, "free_bytes": 40 * GIB, "volume_path": "/disk-a"}]
    assert build(data)["changes"]["capacity_recent"] is None


def test_complete_current_listing_never_resurrects_descendants_of_removed_tree(tmp_path):
    from agent_storage_guard.monitor_store import MonitorStore
    with MonitorStore(tmp_path.resolve()) as store:
        store.record_scan({
            "/projects": {"complete": True, "children_complete": True, "bytes": 40 * GIB},
            "/projects/app": {"complete": True, "children_complete": True, "bytes": 30 * GIB},
            "/projects/app/build": {"complete": True, "bytes": 29 * GIB}}, 60 * GIB, now=NOW - 86400)
        current = store.record_scan({
            "/projects": {"complete": True, "children_complete": True, "bytes": 10 * GIB}}, 80 * GIB, now=NOW)
        assert current["groups"]["/projects/app"]["inferred_absent"]
        view = build({"time": NOW, "config": {"roots": [{"path": "/projects"}]},
                      "capacity": {"time": NOW, "free_bytes": 80 * GIB},
                      "scan": current, "folder_history": store.report_history()})
    assert not any(row["path"] == "/projects/app/build" for row in view["appendix"]["folders"])
    assert not any(action.get("path") == "/projects/app/build" for action in view["actions"])


def test_nominal_capacity_period_rejects_known_different_volume():
    data = payload()
    data["capacity"]["volume_path"] = "/disk-b"
    data["capacity_history"][0]["volume_path"] = "/disk-a"
    view = build(data)
    assert view["period"]["label"] == "Past day"
    assert view["period"]["free_delta_bytes"] is None
    assert view["changes"]["capacity_recent"] is None


def test_device_identity_takes_precedence_over_paths_within_same_volume():
    data = payload()
    data["capacity"].update(volume_device=7, volume_path="/disk/monitor/new")
    data["capacity_history"][0].update(volume_device=7, volume_path="/disk/monitor/old")
    view = build(data)
    assert view["period"]["free_delta_bytes"] == -8 * GIB
    assert view["changes"]["capacity_recent"]["delta_bytes"] == -8 * GIB
    data["capacity_history"][0]["volume_device"] = 8
    view = build(data)
    assert view["period"]["free_delta_bytes"] is None
    assert view["changes"]["capacity_recent"] is None


def test_recent_targeted_scan_cannot_make_old_merged_root_current():
    data = payload()
    data["scan"]["groups"]["/home/test/projects"]["observed_at"] = NOW - 14 * 3600
    data["scan"]["groups"]["/home/test/projects/app"]["observed_at"] = NOW - 14 * 3600
    view = build(data)
    assert view["status"]["folder_fresh"]
    area = next(row for row in view["locations"] if row["label"] == "Projects and worktrees")
    assert area["coverage"] == "older"
    assert area["observed_at"] == NOW - 14 * 3600
    assert area["known_bytes"] == 0
    assert area["delta_bytes"] is None
    assert not any(row["path"].startswith("/home/test/projects") for row in view["changes"]["rows"])


def test_all_unknown_current_folders_do_not_claim_current_changes_are_available():
    data = payload()
    data["capacity_history"] = []
    data["scan"]["groups"] = {"/tmp": {"complete": False}, "/home/test/projects": {"complete": False}}
    view = build(data)
    assert "current folder changes are unknown" in view["changes"]["summary"]
    assert view["changes"]["rows"] == []
