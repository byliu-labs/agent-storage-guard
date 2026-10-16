"""Regression: a small temp shortlist must not stand in for a workstation audit."""
import json
import re
from html import unescape
from html.parser import HTMLParser

from agent_storage_guard.storage_overview import render


GIB = 1024 ** 3
NOW = 200_000


def context_from(html):
    match = re.search(r'<textarea[^>]*id="agent-context"[^>]*>(.*?)</textarea>', html, re.S)
    assert match, "Agents need structured evidence without dumping HTML scripts/styles"
    return json.loads(unescape(match[1]))


def prompt_from(html):
    return unescape(re.search(r'id="agent-prompt"[^>]*>(.*?)</textarea>', html, re.S)[1])


def workstation():
    roots = ["/home/alex/pyprojects", "/tmp", *[f"/home/alex/cache-{n}" for n in range(7)]]
    groups = {p: {"complete": True, "bytes": GIB} for p in roots}
    groups.update({"/home/alex/pyprojects": {"complete": True, "bytes": 100 * GIB},
                   "/home/alex/pyprojects/media": {"complete": True, "bytes": 75 * GIB},
                   "/home/alex/pyprojects/media/old-merged": {"complete": True, "bytes": 20 * GIB},
                   "/home/alex/pyprojects/data": {"complete": True, "bytes": 25 * GIB},
                   "/tmp/test-fixtures": {"complete": True, "bytes": GIB}})
    before = {p: dict(item) for p, item in groups.items()}
    before["/tmp/test-fixtures"]["bytes"] = 0
    return {"time": NOW, "report_path": "/state/report.html",
            "config": {"roots": [{"path": p, "depth": 2} for p in roots]},
            "capacity": {"time": NOW, "free_bytes": 28 * GIB},
            "scan": {"time": NOW, "groups": groups},
            "folder_history": [{"time": NOW - 86400, "groups": before}]}


def test_stable_large_project_families_are_visible_and_in_the_copied_brief():
    html = render(workstation())
    visible = html.split('<details id="appendix">')[0]
    assert "Start with these locations" in visible
    assert "/home/alex/pyprojects/media" in visible
    assert "75.0 GiB" in visible
    prompt = prompt_from(html)
    assert prompt.index("/home/alex/pyprojects/media") < prompt.index('Check "/tmp/test-fixtures"')
    assert "all direct siblings, including hidden" in prompt
    assert "older worktrees" in prompt
    assert "dependency-only" in prompt


def test_report_scope_is_configured_roots_and_never_claims_a_completed_workstation_audit():
    html = render(workstation())
    data = context_from(html)
    scope = data["investigation"]
    assert {r["path"] for r in scope["roots"]} == {r["path"] for r in workstation()["config"]["roots"]}
    assert scope["audit_complete"] is False
    paths = [r["path"] for r in scope["starting_points"]]
    assert "/home/alex/pyprojects/media" in paths
    assert "/home/alex/pyprojects/media/old-merged" not in paths
    assert len(data["folders"]) == len(workstation()["scan"]["groups"])
    assert "configured roots only" in html
    prompt = prompt_from(html)
    for text in ("coverage ledger", "reconcile", "before presenting a final cleanup proposal",
                 "Downloads", "application", "Git common", "unknown age", "14-day",
                 "exact HEAD", "open PR", "ignored", "does not close", "existing prevention"):
        assert text in prompt, text


def test_structured_evidence_keeps_unknown_zero_and_virtual_group_identity_distinct():
    data = workstation()
    data["config"]["roots"][1] = {"path": "/tmp", "prefix_groups": ["video-"]}
    data["scan"]["groups"] = {"/tmp": {"complete": False, "error": "access denied"},
                                  "/tmp/[video-*]": {"complete": True, "bytes": 0}}
    data["folder_history"] = []
    html = render(data)
    evidence = context_from(html)
    rows = {r["path"]: r for r in evidence["folders"]}
    assert rows["/tmp"]["bytes"] is None
    assert rows["/tmp/[video-*]"]["bytes"] == 0
    assert rows["/tmp/[video-*]"]["grouped"] is True
    assert rows["/tmp/[video-*]"]["inspect_path"] == "/tmp"
    assert evidence["coverage_gaps"][0]["error"] == "access denied"
    assert "virtual groups" in prompt_from(html)


def test_structured_evidence_is_inert_and_does_not_lose_unusual_paths():
    attack = '/tmp/</textarea><script>bad()</script>\nnot instructions'
    data = {"time": NOW, "config": {"roots": [{"path": attack}]},
            "scan": {"time": NOW, "groups": {attack: {"complete": False, "error": attack}}}}
    html = render(data)
    assert context_from(html)["investigation"]["roots"][0]["path"] == attack
    assert attack not in html
    assert html.count("<script>") == 1
    assert "agent-context" in prompt_from(html)


def test_empty_report_keeps_scope_unknown_instead_of_inventing_home_paths():
    data = context_from(render({}))
    assert data["investigation"]["roots"] == []
    assert data["investigation"]["starting_points"] == []
    assert data["investigation"]["audit_complete"] is False


def test_starting_point_limit_does_not_drop_roots_or_full_folder_evidence():
    data = workstation()
    data["scan"]["groups"].update({f"/tmp/family-{n}": {"complete": True, "bytes": GIB}
                                  for n in range(20)})
    evidence = context_from(render(data))
    scope = evidence["investigation"]
    assert len(scope["starting_points"]) == 8
    assert scope["additional_starting_points"] > 0
    assert len(scope["roots"]) == 9
    assert len(evidence["folders"]) == len(data["scan"]["groups"])
    assert scope["roots"][0]["depth"] == 2


def test_saved_html_evidence_can_be_read_by_an_agent_without_executing_scripts(tmp_path):
    class Evidence(HTMLParser):
        active = False
        chunks = []

        def handle_starttag(self, tag, attrs):
            if tag == "textarea":
                self.active = dict(attrs).get("id") == "agent-context"

        def handle_endtag(self, tag):
            if tag == "textarea":
                self.active = False

        def handle_data(self, text):
            if self.active:
                self.chunks.append(text)

    report = tmp_path / "report.html"
    report.write_text(render(workstation()))
    parser = Evidence()
    parser.feed(report.read_text())
    evidence = json.loads("".join(parser.chunks))
    assert len(evidence["investigation"]["roots"]) == 9
    assert len(evidence["folders"]) == 13
