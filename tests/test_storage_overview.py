"""Local reports keep the decision view readable and raw evidence tucked away."""
from html.parser import HTMLParser

from agent_storage_guard.storage_overview import render


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_report_has_five_ordered_sections_and_folded_appendix():
    html = render({})
    sections = ["Current status", "What changed", "Where storage is used", "What you can do", "Details and evidence"]
    positions = [html.index(label) for label in sections]
    assert positions == sorted(positions)
    parsed = Elements()
    parsed.feed(html)
    assert ("details", {"id": "appendix"}) in parsed.elements
    assert all("open" not in attrs for tag, attrs in parsed.elements if tag == "details")
    assert "Not measured" in html


def test_local_report_disallows_network_and_keeps_dynamic_text_inert():
    attack = '</script><script>alert("bad")</script><img src="https://evil.invalid/x" onerror="alert(1)">'
    html = render({"time": 1000, "scan": {"time": 1000, "changes": {attack: {"bytes": 1024}},
                                             "groups": {attack: {"complete": False, "error": attack}}}})
    parsed = Elements()
    parsed.feed(html)
    assert not any(tag == "img" for tag, _ in parsed.elements)
    assert attack not in html
    assert "default-src 'none'" in html
    assert "connect-src 'none'" in html
    assert "object-src 'none'" in html
    assert "base-uri 'none'" in html
    assert not any(tag in {"script", "link"} and ("src" in attrs or "href" in attrs)
                   for tag, attrs in parsed.elements)


def test_folder_details_offer_filter_and_safe_copy_without_mutation_actions():
    html = render({"time": 1000, "capacity": {"time": 1000, "free_bytes": 10 * 1024 ** 3},
                   "config": {"warn_free_bytes": 50 * 1024 ** 3},
                   "scan": {"time": 1000, "groups": {"/dev/test outputs": {
                       "bytes": 10 * 1024 ** 3, "complete": True}}}})
    parsed = Elements()
    parsed.feed(html)
    assert any(tag == "input" and attrs.get("id") == "folder-filter" for tag, attrs in parsed.elements)
    assert "Copy path" in html
    assert "window.open" not in html
    assert "fetch(" not in html
    assert "Delete files" not in html


def test_location_reviews_target_every_root_in_the_category():
    gib = 1024 ** 3
    now = 2_000_000
    roots = ["/home/alex/.claude", "/home/alex/.codex"]
    html = render({"time": now, "capacity": {"time": now, "free_bytes": 60 * gib},
                   "config": {"roots": [{"path": path} for path in roots]},
                   "scan": {"time": now, "groups": {path: {"bytes": gib, "complete": True} for path in roots}}})
    parsed = Elements()
    parsed.feed(html)
    assert any(tag == "button" and attrs.get("data-review") == "Agent data" for tag, attrs in parsed.elements)
    assert "Review folders" in html


def test_stale_folder_scan_is_labeled_before_details_are_opened():
    html = render({"time": 100_000, "capacity": {"time": 100_000, "free_bytes": 60 * 1024 ** 3},
                   "scan": {"time": 1_000, "groups": {"/dev": {"bytes": 100, "complete": True}}}})
    before_appendix = html.split('<details id="appendix">')[0]
    assert "Folder measurements are older" in before_appendix


def test_unknown_memory_is_neutral_and_keeps_failure_evidence_in_appendix():
    html = render({"time": 1_000, "capacity": {"time": 1_000, "free_bytes": 60 * 1024 ** 3,
                   "memory": {"pressure": "unknown", "error": "pressure command was unavailable"}}})
    assert "Memory counters are unavailable" in html
    assert "pressure command was unavailable" in html.split('<details id="appendix">')[1]
    assert "<strong class=\"pressure\">Not measured</strong>" in html


def test_virtual_prefix_groups_copy_a_real_parent_location():
    html = render({"time": 1_000, "capacity": {"time": 1_000, "free_bytes": 60 * 1024 ** 3},
                   "config": {"roots": [{"path": "/tmp", "prefix_groups": ["test-"]}]},
                   "scan": {"time": 1_000, "groups": {"/tmp/[test-*]": {"complete": True, "bytes": 100}}}})
    parsed = Elements()
    parsed.feed(html)
    assert any(tag == "button" and attrs.get("data-copy") == "/tmp" for tag, attrs in parsed.elements)
    assert not any(tag == "button" and attrs.get("data-copy") == "/tmp/[test-*]" for tag, attrs in parsed.elements)
    assert "Copy parent path" in html


def test_current_partial_areas_do_not_claim_the_unknown_total_or_reclaim():
    html = render({"time": 1_000, "capacity": {"time": 1_000, "free_bytes": 60 * 1024 ** 3},
                   "config": {"roots": [{"path": "/tmp"}]},
                   "scan": {"time": 1_000, "groups": {"/tmp": {"complete": False},
                      "/tmp/test-output": {"complete": True, "bytes": 5 * 1024 ** 3}}}})
    assert "At least 5.0 GiB" in html
    assert "measured; remainder unknown" in html
    assert "Some folders unmeasured" in html


def test_mixed_current_and_older_subsets_are_shown_separately():
    gib, now = 1024 ** 3, 200_000
    temp = "/var/folders/example/T"
    html = render({"time": now, "capacity": {"time": now, "free_bytes": 60 * gib},
                   "config": {"roots": [{"path": "/tmp"}, {"path": temp}]},
                   "scan": {"time": now, "groups": {"/tmp": {"complete": True, "bytes": 4 * gib},
                                                        temp: {"complete": False}}},
                   "folder_history": [{"time": now - 86400, "groups": {
                       temp: {"complete": False}, f"{temp}/old-output": {"complete": True, "bytes": 38 * gib}}}]})
    assert "4.0 GiB measured now" in html
    assert "Earlier subset 38.0 GiB" in html
    assert "current total unknown" in html
    assert "42.0 GiB" not in html


def test_useful_folder_day_survives_short_capacity_history_with_honest_secondary_trend():
    gib, now = 1024 ** 3, 200_000
    html = render({"time": now, "capacity": {"time": now, "free_bytes": 30 * gib},
                   "config": {"roots": [{"path": "/tmp"}]},
                   "scan": {"time": now, "groups": {"/tmp": {"complete": True, "bytes": 5 * gib}}},
                   "folder_history": [{"time": now - 84240, "groups": {"/tmp": {"complete": True, "bytes": 3 * gib}}}],
                   "capacity_history": [{"time": now - 1020, "free_bytes": 31 * gib},
                                        {"time": now, "free_bytes": 30 * gib}]})
    assert "Folder changes over 23.4 hours" in html
    assert "Recent free space: -1.0 GiB over 17 minutes" in html
    assert "No comparable capacity samples" not in html


def test_report_can_reload_latest_saved_file_without_claiming_a_new_scan():
    html = render({})
    assert 'id="reload-report"' in html
    assert "Reload saved report" in html
    assert "window.location.reload()" in html
    assert "Reload shows the latest saved measurements" in html
    assert "Finder, choose Go → Go to Folder, then paste" in html

