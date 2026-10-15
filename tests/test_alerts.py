from agent_storage_guard import alerts

GIB = 1024 ** 3


def capacity(t, free=40, pressure="normal", swap=10, boot=1):
    return {"time": t, "free_bytes": free * GIB, "memory": {"supported": True, "pressure": pressure, "swap_used_bytes": swap * GIB, "boot_time": boot}}


def step(current, previous=None, state=None, scan=None):
    return alerts.evaluate(current, previous, scan or {}, {}, state or {}, current["time"])


def test_reserve_requires_two_samples_and_action_explains_shortfall():
    a, state = step(capacity(1000))
    assert not a
    a, state = step(capacity(1300), capacity(1000), state)
    assert len(a) == 1
    assert a[0]["title"] == "Free space is below your 50.0 GiB reserve"
    assert "10.0 GiB short" in a[0]["message"]
    assert "recent growers" in a[0]["action"]


def test_duplicate_samples_do_not_create_an_alert():
    a, state = step(capacity(1000))
    a, state = step(capacity(1000), capacity(1000), state)
    assert not a


def test_critical_escalates_without_waiting_for_cooldown():
    a, state = step(capacity(1300), capacity(1000))
    state = alerts.mark_delivered(state, a[0], 1300)
    a, state = step(capacity(1600, 20), capacity(1300), state)
    assert a[0]["level"] == "urgent"
    assert alerts.delivery_candidates(a, state, 1600) == a


def test_recovery_needs_margin_and_two_samples_then_can_alert_again():
    a, state = step(capacity(1300), capacity(1000))
    state = alerts.mark_delivered(state, a[0], 1300)
    a, state = step(capacity(1600, 51), capacity(1300), state)
    assert a
    a, state = step(capacity(1900, 53), capacity(1600, 51), state)
    assert a
    a, state = step(capacity(2200, 53), capacity(1900, 53), state)
    assert not a
    a, state = step(capacity(2500), capacity(2200, 53), state)
    assert not a
    a, state = step(capacity(2800), capacity(2500), state)
    assert alerts.delivery_candidates(a, state, 2800)


def test_swap_and_uptime_alone_do_not_warn_but_sustained_pressure_does():
    a, state = step(capacity(1300, 80, swap=12), capacity(1000, 80, swap=10))
    assert not a
    a, state = step(capacity(1600, 80, "warning", 13), capacity(1300, 80), state)
    assert not a
    a, state = step(capacity(1900, 80, "warning", 14), capacity(1600, 80, "warning", 13), state)
    assert a[0]["key"] == "memory"
    assert "1.0 GiB" in a[0]["message"]
    assert "save" in a[0]["action"].lower()
    assert "normally" in a[0]["action"]


def test_memory_gap_reboot_unknown_are_not_two_pressure_checks():
    for previous in [capacity(1000, 80, "warning", boot=2), capacity(1000, 80, "unknown"), capacity(1, 80, "warning")]:
        a, _ = step(capacity(1300, 80, "warning"), previous)
        assert not a


def test_critical_memory_does_not_require_swap_growth():
    a, _ = step(capacity(1300, 80, "critical"))
    assert a[0]["level"] == "urgent"
    assert "Activity Monitor" in a[0]["action"]


def test_coverage_is_one_group_and_static_gap_is_not_resent():
    scan = {"time": 1000, "groups": {"/tmp/jobs": {"complete": False}, "/tmp/jobs/[other]": {"complete": False}, "/home/example/.Trash": {"complete": False}}, "changes": {}}
    a, state = step(capacity(1000, 80), scan=scan)
    assert len(a) == 1
    assert "2 locations" in a[0]["title"]
    state = alerts.mark_delivered(state, a[0], 1000)
    scan["time"] = 100000
    a, state = step(capacity(100000, 80), state=state, scan=scan)
    assert not alerts.delivery_candidates(a, state, 100000)


def test_growth_reports_actual_interval_and_deduplicates_parent_child():
    changes = {p: {"bytes": 10 * GIB, "hour_delta_bytes": 3 * GIB, "hour_elapsed_seconds": 4200} for p in ["/home/example/dev", "/home/example/dev/worktrees"]}
    scan = {"time": 1000, "groups": {}, "changes": changes}
    a, state = step(capacity(1000, 80), scan=scan)
    assert len(a) == 1
    assert "worktrees" in a[0]["title"]
    assert "1.2 hours" in a[0]["message"]
    assert "Trash" in a[0]["action"]
    state = alerts.mark_delivered(state, a[0], 1000)
    a, state = step(capacity(1300, 80), state=state, scan=scan)
    assert not alerts.delivery_candidates(a, state, 1300)


def test_failed_delivery_is_not_suppressed_and_stale_alert_not_sent():
    a, state = step(capacity(1300), capacity(1000))
    assert alerts.delivery_candidates(a, state, 1300)
    assert alerts.delivery_candidates(a, state, 1301)
    assert not alerts.delivery_candidates(a, state, 4000)


def test_reserve_above_threshold_during_hysteresis_does_not_claim_below():
    a, state = step(capacity(1300), capacity(1000))
    a, state = step(capacity(1600, 51), capacity(1300), state)
    assert "below" not in a[0]["title"]
    assert "recovery" in a[0]["title"]


def test_stale_alert_is_not_reported_as_current():
    a, state = step(capacity(1300), capacity(1000))
    a, _ = alerts.evaluate(capacity(1300), capacity(1000), {}, {}, state, 3000)
    assert not a


def test_unknown_memory_is_not_false_recovery_but_expires():
    a, state = step(capacity(1300, 80, "critical"))
    a, state = step(capacity(1600, 80, "unknown"), capacity(1300, 80, "critical"), state)
    assert a
    assert "memory" in state["active"]
    a, state = step(capacity(2200, 80, "unknown"), capacity(1600, 80, "unknown"), state)
    assert not a


def test_healthy_memory_recovery_then_recurrence_can_alert():
    a, state = step(capacity(1300, 80, "critical"))
    state = alerts.mark_delivered(state, a[0], 1300)
    a, state = step(capacity(1600, 80), capacity(1300, 80, "critical"), state)
    assert a
    a, state = step(capacity(1900, 80), capacity(1600, 80), state)
    assert not a
    a, state = step(capacity(2200, 80, "critical"), capacity(1900, 80), state)
    assert alerts.delivery_candidates(a, state, 2200)


def test_growth_lifetime_is_observation_time_not_tick_time():
    scan = {"time":1000,"groups":{},"changes":{"/tmp/build": {"bytes":3*GIB,"hour_delta_bytes":3*GIB,"hour_elapsed_seconds":3600}}}
    a, state = step(capacity(1000,80), scan=scan)
    a, _ = step(capacity(11000,80), state=state, scan=scan)
    assert not a


def test_new_coverage_gap_is_actionable_immediately_but_partial_recovery_is_not():
    scan = {"time":1000,"groups":{"/tmp/one":{"complete":False}},"changes":{}}
    a,state = step(capacity(1000,80),scan=scan)
    state = alerts.mark_delivered(state,a[0],1000)
    scan = {"time":1300,"groups":{"/tmp/one":{"complete":False},"/tmp/two":{"complete":False}},"changes":{}}
    a,state = step(capacity(1300,80),state=state,scan=scan)
    assert alerts.delivery_candidates(a,state,1300)
    state = alerts.mark_delivered(state,a[0],1300)
    scan = {"time":1600,"groups":{"/tmp/two":{"complete":False}},"changes":{}}
    a,state = step(capacity(1600,80),state=state,scan=scan)
    assert not alerts.delivery_candidates(a,state,1600)


def test_back_to_back_manual_checks_do_not_prove_sustained_pressure_or_reserve():
    a, state = step(capacity(1000,40,"warning"))
    a, state = step(capacity(1000.1,40,"warning"),capacity(1000,40,"warning"),state)
    assert not a


def test_first_status_does_not_relabel_stale_capacity_as_new_incident():
    a,state = alerts.evaluate(capacity(1000,10,"critical"),None,{}, {},{},4000)
    assert not a


def test_alert_observation_time_is_measurement_time_not_report_time():
    a,_ = alerts.evaluate(capacity(1300,10,"critical"),None,{}, {},{},1400)
    assert all(item["observed_at"] == 1300 for item in a)
