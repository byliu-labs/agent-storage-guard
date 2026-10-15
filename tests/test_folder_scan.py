from pathlib import Path

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
