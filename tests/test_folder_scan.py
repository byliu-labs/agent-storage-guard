from pathlib import Path
import sys

from agent_storage_guard.folder_scan import scan_root, scan_roots


def test_real_native_scan_groups_and_does_not_follow_symlinks(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "large").write_bytes(b"x" * 65536)
    root = tmp_path / "scratch"
    root.mkdir()
    for name in ("test-one", "test-two"):
        child = root / name
        child.mkdir()
        (child / "fixture").write_bytes(b"x" * 8192)
    (root / "linked").symlink_to(outside, target_is_directory=True)
    groups = scan_root({"path": str(root), "depth": 1, "prefix_groups": ["test-"]}, now=100)
    assert groups[str(root) + "/[test-*]"]["entries"] == 2
    assert groups[str(root) + "/[test-*]"]["bytes"] >= 16384
    assert groups[str(root)]["bytes"] < 65536
    assert all(item["complete"] and item["observed_at"] == 100 for item in groups.values())


def test_missing_or_symlink_ancestor_is_unknown(tmp_path):
    root = tmp_path / "real"
    root.mkdir()
    (root / "child").mkdir()
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    for path in (tmp_path / "missing", link / "child"):
        item = scan_root({"path": str(path)})[str(path)]
        assert item["complete"] is False
        assert "bytes" not in item


def test_complete_listing_marks_children_and_empty_prefixes(tmp_path):
    (tmp_path / "child").mkdir()
    groups = scan_root({"path": str(tmp_path), "depth": 1})
    assert groups[str(tmp_path)]["children_complete"] is True
    assert groups[str(tmp_path / "child")]["children_complete"] is False
    grouped = scan_root({"path": str(tmp_path), "prefix_groups": ["test-"]})
    assert grouped[str(tmp_path) + "/[test-*]"]["bytes"] == 0


def test_total_scan_deadline_preserves_unvisited_roots_as_unknown(tmp_path):
    roots = [{"path": str(tmp_path / str(index))} for index in range(3)]
    result = scan_roots(roots, total_timeout=0.000001)
    assert set(result) == {item["path"] for item in roots}
    assert not any(item["complete"] for item in result.values())


def test_real_native_scan_timeout_is_unknown(tmp_path):
    (tmp_path / "fixture").write_bytes(b"x" * 8192)
    groups = scan_root({"path": str(tmp_path)}, timeout=0.000001)
    assert groups[str(tmp_path)]["complete"] is False
    assert "bytes" not in groups[str(tmp_path)]


def test_denied_child_is_not_a_complete_zero(tmp_path):
    import os
    import pytest
    if os.getuid() == 0:
        pytest.skip("root bypasses fixture directory permissions")
    restricted = tmp_path / "denied"
    restricted.mkdir()
    (restricted / "fixture").write_bytes(b"x" * 8192)
    restricted.chmod(0)
    try:
        groups = scan_root({"path": str(tmp_path)})
    finally:
        restricted.chmod(0o700)
    assert groups[str(tmp_path)]["complete"] is False
    assert not groups.get(str(restricted), {}).get("complete", False)


def test_grouping_rejects_double_counting_nested_totals(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="exactly 1"):
        scan_root({"path": str(tmp_path), "depth": 2, "prefix_groups": ["test-"]})


def test_macos_system_var_alias_keeps_original_keys(tmp_path):
    import sys
    import pytest
    if sys.platform != "darwin" or not str(tmp_path).startswith("/private/var/"):
        pytest.skip("macOS system /var alias fixture")
    logical = Path("/var").joinpath(*tmp_path.parts[3:])
    (tmp_path / "child").mkdir()
    (tmp_path / "child" / "fixture").write_bytes(b"x" * 8192)
    groups = scan_root({"path": str(logical)})
    assert groups[str(logical)]["complete"] is True
    assert groups[str(logical / "child")]["bytes"] >= 8192
    assert not any(key.startswith("/private/var/") for key in groups)


def native_fixture(tmp_path, monkeypatch, body):
    from agent_storage_guard import folder_scan
    executable = tmp_path / "native-scanner"
    executable.write_text(f"#!{sys.executable}\nimport sys,time\nfrom pathlib import Path\nroot=Path(sys.argv[-1])\n" + body)
    executable.chmod(0o700)
    monkeypatch.setattr(folder_scan, "_DU", str(executable), raising=False)


def test_large_native_stream_is_aggregated_without_a_raw_output_ceiling(tmp_path, monkeypatch):
    native_fixture(tmp_path, monkeypatch,
                   "for i in range(60000): print(f'4\\t{root}/test-{i:05d}')\nprint(f'240000\\t{root}')\n")
    groups = scan_root({"path": str(tmp_path), "prefix_groups": ["test-"]}, timeout=10)
    assert groups[str(tmp_path)]["complete"]
    assert groups[str(tmp_path) + "/[test-*]"]["bytes"] == 240000 * 1024
    assert len(groups) == 3


def test_deadline_keeps_completed_subtrees_and_marks_root_incomplete(tmp_path, monkeypatch):
    child = tmp_path / "finished"
    child.mkdir()
    native_fixture(tmp_path, monkeypatch, "print(f'8\\t{root}/finished',flush=True)\ntime.sleep(5)\n")
    groups = scan_root({"path": str(tmp_path)}, timeout=.5)
    assert groups[str(child)]["complete"] and groups[str(child)]["bytes"] == 8192
    assert not groups[str(tmp_path)]["complete"]
    assert "deadline" in groups[str(tmp_path)]["error"]


def test_permission_failure_keeps_readable_siblings_and_original_reason(tmp_path, monkeypatch):
    native_fixture(tmp_path, monkeypatch,
                   "print(f'8\\t{root}/readable')\nprint(f'0\\t{root}/denied')\nprint(f'8\\t{root}')\n"
                   "print(f'du: {root}/denied: Operation not permitted',file=sys.stderr)\nsys.exit(1)\n")
    groups = scan_root({"path": str(tmp_path)})
    assert groups[str(tmp_path / "readable")]["complete"]
    assert not groups[str(tmp_path / "denied")]["complete"]
    assert "permission" in groups[str(tmp_path)]["error"].lower()


def test_denied_root_explains_access_instead_of_missing_root_total(tmp_path, monkeypatch):
    native_fixture(tmp_path, monkeypatch,
                   "print(f'du: {root}: Operation not permitted',file=sys.stderr)\nsys.exit(1)\n")
    groups = scan_root({"path": str(tmp_path)})
    assert not groups[str(tmp_path)]["complete"]
    assert "Operation not permitted" in groups[str(tmp_path)]["error"]


def test_detail_budget_does_not_discard_a_complete_root_total(tmp_path, monkeypatch):
    native_fixture(tmp_path, monkeypatch,
                   "for i in range(12500): print(f'4\\t{root}/item-{i:05d}')\nprint(f'50000\\t{root}')\n")
    groups = scan_root({"path": str(tmp_path)}, timeout=10)
    assert groups[str(tmp_path)]["complete"]
    assert groups[str(tmp_path)]["bytes"] == 50000 * 1024
    assert not groups[str(tmp_path)]["children_complete"]
    assert len(groups) <= 12000


def test_slow_root_cannot_consume_the_whole_pass_before_fast_cache(tmp_path, monkeypatch):
    slow, fast = tmp_path / "slow", tmp_path / "fast-cache"
    slow.mkdir()
    fast.mkdir()
    native_fixture(tmp_path, monkeypatch,
                   "if root.name == 'slow': time.sleep(5)\nprint(f'8\\t{root}')\n")
    groups = scan_roots([{"path": str(slow)}, {"path": str(fast)}], timeout=5, total_timeout=1)
    assert groups[str(fast)]["complete"] and groups[str(fast)]["bytes"] == 8192
    assert not groups[str(slow)]["complete"]


def test_permission_and_deadline_are_both_retained_as_structured_causes(tmp_path):
    from agent_storage_guard.scan_totals import ScanTotals
    totals = ScanTotals(tmp_path,1,[],100)
    totals.error(f"du: {tmp_path}/denied: Operation not permitted".encode())
    group = totals.finish(None,"Folder scan exceeded deadline",1000)[str(tmp_path)]
    assert group["error_kind"] == "permission_denied"
    assert group["timed_out"] is True
    assert group["complete"] is False


def test_missing_root_has_structured_io_cause(tmp_path):
    path = tmp_path / "missing"
    group = scan_root({"path":str(path)})[str(path)]
    assert group["error_kind"] == "io_error"
    assert group["timed_out"] is False


def test_retry_spends_spare_budget_to_finish_a_slow_root_instead_of_restarting_all(monkeypatch):
    from types import SimpleNamespace
    from agent_storage_guard import folder_scan
    clock=[0.0]
    visits=[]
    monkeypatch.setattr(folder_scan,"time",SimpleNamespace(monotonic=lambda:clock[0],time=lambda:1000+clock[0]))
    def measured(spec,timeout):
        path=spec["path"]
        duration=7 if "slow" in path else .1
        complete=duration<=timeout
        clock[0]+=min(duration,timeout)
        visits.append((path,timeout))
        return {path:{"complete":complete,"bytes":1024,"error":"" if complete else "deadline","observed_at":1000+clock[0]}}
    monkeypatch.setattr(folder_scan,"scan_root",measured)
    specs=[{"path":"/synthetic/"+name} for name in ("slow-a","slow-b","fast-a","fast-b")]
    result=folder_scan.scan_roots(specs,timeout=10,total_timeout=20)
    assert [path for path,_ in visits[:4]]==[spec["path"] for spec in specs]
    assert result["/synthetic/slow-a"]["complete"]
    assert not result["/synthetic/slow-b"]["complete"]
    assert clock[0]<=20
