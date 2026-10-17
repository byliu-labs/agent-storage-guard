"""Synthetic CI monitoring across real CLI, files, processes and SQLite history."""
import json
from html.parser import HTMLParser
from pathlib import Path
import plistlib
import subprocess
import sys
import time

from agent_storage_guard import installation
from agent_storage_guard.monitor_store import MonitorStore


def invoke(state, *arguments):
    result = subprocess.run([sys.executable, "-m", "agent_storage_guard", "--state",
                             str(state), "monitor", *arguments], capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result


class Context(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        self.active = tag == "textarea" and dict(attrs).get("id") == "agent-context"

    def handle_endtag(self, tag):
        if tag == "textarea":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.chunks.append(data)


def test_ci_scan_during_job_preserves_full_evidence_and_original_baseline(tmp_path):
    state, root = tmp_path / "guard-state", tmp_path / "ci"
    stable, work = root / "snapshots", root / "workspaces"
    stable.mkdir(parents=True)
    work.mkdir()
    (stable / "retained-snapshot").write_bytes(b"s" * 262144)
    invoke(state, "init", "--root", str(root), "--root", str(stable))
    path = state / "monitor/config.json"
    config = json.loads(path.read_text())
    config.update(scan_timeout_seconds=2, scan_total_timeout_seconds=4,
                  tick_interval_seconds=60, scan_interval_seconds=900)
    path.write_text(json.dumps(config))
    invoke(state, "scan")
    with MonitorStore(state / "monitor") as store:
        baseline = store.baselines()
    writer = subprocess.Popen([sys.executable, "-c",
        "import pathlib,sys,time\np=pathlib.Path(sys.argv[1])\n"
        "for i in range(10000):\n (p / ('part-%d' % (i % 32))).write_bytes(b'x'*4096)\n time.sleep(.01)\n",
        str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not (work / "part-0").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert (work / "part-0").exists()
        started = time.monotonic()
        result = json.loads(invoke(state, "scan", "--json").stdout)
        assert time.monotonic() - started < 12
        assert writer.poll() is None
        groups = result["scan"]["groups"]
        assert groups[str(stable)]["complete"]
        invoke(state, "report")
        parser = Context()
        parser.feed((state / "monitor/report.html").read_text())
        evidence = json.loads("".join(parser.chunks))
        assert {r["path"] for r in evidence["investigation"]["roots"]} == {str(root), str(stable)}
        assert str(stable) in {r["path"] for r in evidence["investigation"]["starting_points"]}
        assert len(evidence["folders"]) == len(groups)
        assert sum(r["bytes"] for r in evidence["locations"]) == groups[str(root)]["bytes"]
        assert evidence["investigation"]["audit_complete"] is False
        stable.rename(root / "preserved-snapshots")
        invoke(state, "scan")
        with MonitorStore(state / "monitor") as store:
            assert store.baselines()[str(stable)] == baseline[str(stable)]
            assert not store.latest_scan()["groups"][str(stable)]["complete"]
        invoke(state, "report")
        parser = Context()
        parser.feed((state / "monitor/report.html").read_text())
        evidence = json.loads("".join(parser.chunks))
        assert str(stable) in {r["path"] for r in evidence["coverage_gaps"]}
    finally:
        writer.terminate()
        try:
            writer.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            writer.kill()
            writer.communicate(timeout=5)


def test_ci_activation_and_rollback_preserve_unrelated_service(tmp_path):
    home, state = tmp_path / "home", tmp_path / "guard-state"
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    unrelated = agents / "example.unrelated.plist"
    original = plistlib.dumps({"Label": "example.unrelated"})
    unrelated.write_bytes(original)
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps(["example.unrelated"]))
    controller = tmp_path / "launchctl"
    controller.write_text(f"#!{sys.executable}\n" +
        "import json,pathlib,plistlib,sys\n" +
        f"p=pathlib.Path({str(jobs)!r})\n" +
        "jobs=json.loads(p.read_text()); action=sys.argv[1]; label=sys.argv[2].split('/')[-1]\n"
        "if action=='print': sys.exit(0 if label in jobs else 113)\n"
        "if action=='bootstrap': jobs.append(plistlib.loads(pathlib.Path(sys.argv[3]).read_bytes())['Label'])\n"
        "if action=='bootout': jobs.remove(label)\n"
        "p.write_text(json.dumps(jobs))\n")
    controller.chmod(0o700)
    cli = tmp_path / "guard"
    cli.write_text("#!/bin/sh\nexit 0\n")
    cli.chmod(0o700)
    result = installation.install(home, cli, state, True, controller,
                                  intervals={"tick": 60, "scan": 900})
    for command, seconds in (("tick", 60), ("scan", 900)):
        job = plistlib.loads((agents / f"local.agent-storage-guard.{command}.plist").read_bytes())
        assert job["StartInterval"] == seconds
    assert set(json.loads(jobs.read_text())) == {"example.unrelated", *installation.LABELS}
    installation.rollback(Path(result["manifest"]), controller)
    assert unrelated.read_bytes() == original
    assert json.loads(jobs.read_text()) == ["example.unrelated"]
    assert {p.name for p in agents.iterdir()} == {unrelated.name}
