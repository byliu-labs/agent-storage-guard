"""One private, offline HTML report: decisions first, supporting evidence on demand."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from .report_insights import build


def _text(value: object) -> str:
    return escape(str(value if value is not None else "Not measured"), quote=True)


def _bytes(value: int | float | None, *, signed: bool = False) -> str:
    if value is None:
        return "Not measured"
    number = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    index = 0
    while abs(number) >= 1024 and index < len(units) - 1:
        number /= 1024
        index += 1
    return f"{number:+.1f} {units[index]}" if signed else f"{number:,.1f} {units[index]}"


def _time(value: float | None) -> str:
    if value is None:
        return "Not measured"
    return datetime.fromtimestamp(value, timezone.utc).strftime("%d %b %Y, %H:%M UTC")


def _duration(value: float | None) -> str:
    if value is None:
        return "not measured"
    if value >= 172800:
        return f"{value / 86400:.1f} days"
    if value < 3600:
        return f"{value / 60:.0f} minutes"
    return f"{value / 3600:.1f} hours"


def _review(path: str = "", label: str = "folders") -> str:
    return (f'<button class="review" type="button" data-review="{_text(path)}" '
            f'aria-label="Review {_text(label)}">Review folders <span aria-hidden="true">↗</span></button>')


def _status(view: dict) -> str:
    status, memory = view["status"], view["status"].get("memory", {})
    shortfall = status.get("shortfall_bytes")
    disk_note = (f'{_bytes(shortfall)} below your reserve' if shortfall and shortfall > 0
                 else "Reserve met" if status.get("capacity_fresh") else "Current capacity is unknown")
    memory_note = f'Swap {_bytes(memory.get("swap_used_bytes"))} · uptime {_duration(memory.get("uptime_seconds"))}'
    if memory.get("swap_used_bytes") is None:
        memory_note = "Memory counters are unavailable for this measurement."
    trend = memory.get("swap_delta_bytes")
    if trend is not None:
        memory_note += f' · swap {_bytes(trend, signed=True)} in {_duration(memory.get("elapsed_seconds"))}'
    pressure = memory.get("pressure") or "Not measured"
    if pressure in {"unknown", "unavailable"}:
        pressure = "Not measured"
    freshness = "Measured" if status.get("capacity_fresh") else "Older measurement — refresh before deciding"
    return f'''<section aria-labelledby="status-title" id="current-status">
      <div class="section-label"><span>01</span><h2 id="status-title">Current status</h2></div>
      <div class="status-banner"><span class="status-dot" aria-hidden="true"></span><h3>{_text(status.get("headline"))}</h3></div>
      <div class="metric-grid">
        <article class="metric"><p class="eyebrow">Available disk space</p><strong>{_bytes(status.get("free_bytes"))}</strong>
          <p>{_text(disk_note)}</p><p class="secondary">Reserve {_bytes(status.get("reserve_bytes"))}</p></article>
        <article class="metric"><p class="eyebrow">Memory pressure</p><strong class="pressure">{_text(pressure)}</strong>
          <p>{_text(memory_note)}</p><p class="secondary">Swap size alone is not a reason to restart.</p></article>
      </div><p class="timestamp">{_text(freshness)} · {_time(status.get("measured_at"))}</p>
    </section>'''


def _changes(view: dict) -> str:
    changes, period = view.get("changes", {}), view.get("period", {})
    free_delta = period.get("free_delta_bytes")
    metric = (f'Folder changes over {_duration(period.get("elapsed_seconds"))}' if period.get("started_at") is not None
              else "Building your history") if free_delta is None else f'{_bytes(free_delta, signed=True)} free space'
    start = period.get("capacity_started_at", period.get("started_at")) if free_delta is not None else period.get("started_at")
    end = period.get("capacity_ended_at", period.get("ended_at")) if free_delta is not None else period.get("ended_at")
    interval = (f'{_time(start)} → {_time(end)}' if start is not None else "A second comparable measurement is needed.")
    rows = []
    for row in changes.get("rows", [])[:4]:
        rows.append(f'<li><span>{_text(row.get("label"))}<small>{_text(_duration(row.get("period_elapsed_seconds")))}</small></span>'
                    f'<strong>{_bytes(row.get("delta_bytes"), signed=True)}</strong></li>')
    growers = f'<ul class="growers">{"".join(rows)}</ul>' if rows else ""
    recent = changes.get("capacity_recent")
    recent_note = (f'<p class="secondary">Recent free space: {_bytes(recent["delta_bytes"], signed=True)} '
                   f'over {_text(_duration(recent["elapsed_seconds"]))}.</p>' if recent and free_delta is None else "")
    return f'''<section aria-labelledby="changes-title">
      <div class="section-label"><span>02</span><h2 id="changes-title">What changed</h2></div>
      <div class="change-card"><div><p class="eyebrow">{_text(changes.get("period_label", period.get("label", "Available history")))}</p>
        <h3>{_text(metric)}</h3><p>{_text(changes.get("summary", period.get("explanation", "No comparable measurements yet.")))}</p>
        <p class="timestamp">{_text(interval)}</p>{recent_note}</div>{growers}</div>
    </section>'''


def _areas(view: dict) -> str:
    locations = view.get("locations", [])[:7]
    maximum = max((item.get("bytes") or item.get("known_bytes") or item.get("older_known_bytes") or 0 for item in locations), default=1) or 1
    rows = []
    for item in locations:
        coverage = item.get("coverage", "unknown")
        known = item.get("bytes") if item.get("bytes") is not None else item.get("known_bytes") if coverage == "partial" else None
        if coverage == "older_partial":
            known = item.get("known_bytes") or item.get("older_known_bytes")
        width = min(100, max(0, (known or 0) / maximum * 100))
        size = f'At least {_bytes(known)}<small>measured; remainder unknown</small>' if coverage == "partial" else _bytes(item.get("bytes"))
        if coverage == "older_partial":
            size = (f'{_bytes(item["known_bytes"])} measured now<small>Earlier subset {_bytes(item.get("older_known_bytes"))}</small>'
                    if item.get("known_bytes") else f'Last measured subset {_bytes(known)}')
            size += '<small>current total unknown</small>'
        if coverage in {"older", "older_partial"}:
            size += f'<small>{_time(item.get("observed_at"))}</small>'
        delta = (f'{_bytes(item.get("known_delta_bytes"), signed=True)}<small>measured portion only</small>'
                 if coverage in {"partial", "older_partial"} and item.get("known_delta_bytes") is not None else _bytes(item.get("delta_bytes"), signed=True))
        label = {"older": "Earlier measurement", "unknown": "Not measured", "partial": "Some folders unmeasured",
                 "older_partial": "Earlier partial measurement"}.get(coverage, "")
        status = f'<span class="quality">{label}</span>' if label else ""
        bar = f'<div class="size-bar" aria-hidden="true"><i style="width:{width:.1f}%"></i></div>' if known is not None else ""
        rows.append(f'''<tr><th scope="row">{_text(item.get("label"))}{status}
          {bar}</th>
          <td>{size}</td><td>{delta}
          <small>{_text(_duration(item.get("period_elapsed_seconds")))}</small></td>
          <td>{_review(item.get("path") or item.get("label", ""), item.get("label", "folders"))}</td></tr>''')
    table = (f'<div class="table-scroll"><table class="areas"><thead><tr><th>Location</th><th>Measured size</th>'
             f'<th>Change</th><th><span class="sr-only">Review</span></th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
             if rows else '<p class="empty">No folder measurements yet. Refresh the folder scan to build a baseline.</p>')
    return f'''<section aria-labelledby="areas-title">
      <div class="section-label"><span>03</span><h2 id="areas-title">Where storage is used</h2></div>
      <p class="secondary">Monitored locations. Nested folders are counted once.</p>
      <p class="timestamp">{"Folder scan measured" if view["status"].get("folder_fresh") else "Folder measurements are older or unavailable — refresh before cleanup"} · {_time(view["status"].get("folder_measured_at"))}</p>
      {table}
    </section>'''


def _actions(view: dict) -> str:
    actions = []
    for index, item in enumerate(view.get("actions", []), 1):
        control = _review(item["path"], item.get("title", "folders")) if item.get("path") else ""
        actions.append(f'''<li><span class="action-number" aria-hidden="true">{index}</span><div>
          <h3>{_text(item.get("title"))}</h3><p>{_text(item.get("body"))}</p>{control}</div></li>''')
    content = f'<ol class="actions">{"".join(actions)}</ol>' if actions else '<p>No action is suggested from the available evidence.</p>'
    return f'''<section aria-labelledby="actions-title">
      <div class="section-label"><span>04</span><h2 id="actions-title">What you can do</h2></div>
      {content}
    </section>'''


def _folder_row(item: dict) -> str:
    path = item.get("path", "")
    copy_path = item.get("inspect_path") or path
    copy_label = "Copy parent path" if item.get("grouped") else "Copy path"
    group_note = '<small>Grouped folders · inspect their parent location</small>' if item.get("grouped") else ""
    searchable = f'{item.get("label", "")} {path} {item.get("category", "")}'
    return f'''<tr data-search="{_text(searchable)}"><th scope="row">{_text(item.get("label"))}
      <span class="folder-path">{_text(path)}</span>{group_note}<button class="copy" type="button" data-copy="{_text(copy_path)}">{copy_label}</button></th>
      <td>{_bytes(item.get("bytes"))}</td><td>{_bytes(item.get("delta_bytes"), signed=True)}
        <small>{_text(_duration(item.get("period_elapsed_seconds")))}</small>
        <small>{_time(item.get("period_started_at"))}</small></td>
      <td>{_bytes(item.get("baseline_delta_bytes"), signed=True)}</td><td>{_text(item.get("coverage", "unknown"))}
        <small>{_time(item.get("observed_at"))}</small></td></tr>'''


def _appendix(view: dict) -> str:
    data = view.get("appendix", {})
    folders = "".join(_folder_row(row) for row in data.get("folders", []))
    gaps = "".join(f'<li><code>{_text(gap.get("path"))}</code><br>{_text(gap.get("error"))}</li>'
                   for gap in data.get("coverage_gaps", [])) or "<li>No coverage gaps reported.</li>"
    caveats = "".join(f'<li>{_text(note)}</li>' for note in data.get("caveats", []))
    memory = view["status"].get("memory", {})
    memory_error = memory.get("error", memory.get("reason"))
    memory_evidence = (f'<h3>Memory measurement</h3><p class="secondary">{_text(memory_error)}</p>'
                       if memory_error else "")
    worktrees = data.get("worktrees", {})
    tree_rows = "".join(f'<li><code>{_text(item.get("path", item.get("worktree", "?")))}</code> · '
                        f'{_text(item.get("branch", "detached"))}'
                        f'{" · stale registration" if item.get("prunable") else ""}</li>'
                        for item in worktrees.get("worktrees", []))
    inventory = (f'<details class="nested"><summary>Git worktree registrations ({len(worktrees.get("worktrees", []))})</summary>'
                 f'<p class="secondary">A registration does not prove disk usage or cleanup safety.</p><ul>{tree_rows}</ul></details>'
                 if tree_rows else "")
    return f'''<section aria-labelledby="details-title" class="appendix-section">
      <div class="section-label"><span>05</span><h2 id="details-title">Details and evidence</h2></div>
      <details id="appendix"><summary>Folder paths, measurement times and scan coverage <span class="secondary">Expand to investigate</span></summary>
        <div class="appendix-content"><label for="folder-filter">Find a folder</label>
          <input id="folder-filter" type="search" placeholder="Filter by folder name or path" autocomplete="off">
          <p id="filter-count" class="secondary" role="status">{len(data.get("folders", []))} folders</p>
          <div class="table-scroll"><table id="folder-table"><thead><tr><th>Folder</th><th>Measured size</th><th>Recent change</th>
            <th>Since baseline</th><th>Evidence</th></tr></thead><tbody>{folders}</tbody></table></div>
          <p id="copy-status" class="secondary" role="status" aria-live="polite"></p>
          <textarea id="copy-fallback" aria-label="Path to copy" readonly hidden></textarea>
          <p class="secondary">To inspect a copied path in Finder, choose Go → Go to Folder, then paste. Grouped folders copy their real parent location.</p>
          <h3>Scan coverage</h3><ul class="coverage">{gaps}</ul>
          <h3>Refresh this report</h3><p class="secondary">The installed monitor updates measurements on its schedule. To request a new capacity check and folder scan from Terminal:</p>
          <p><code>agent-storage-guard monitor tick</code><br><code>agent-storage-guard monitor scan</code><br><code>agent-storage-guard monitor report --open</code></p>
          {memory_evidence}<h3>How to read this evidence</h3><ul class="caveats">{caveats}</ul>{inventory}
        </div></details>
    </section>'''


_STYLE = """
:root{color-scheme:light dark;--bg:#f4f5f7;--paper:#fff;--ink:#18212c;--muted:#596674;--line:#e0e5ea;--accent:#266b60;--soft:#e9f3ef;--warn:#955815;--warn-soft:#fff3df;--shadow:0 12px 40px #22334408}

*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1060px;margin:42px auto;padding:0 28px 40px}
header{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:28px}
.brand{font-size:13px;font-weight:650;letter-spacing:.04em;color:var(--muted)}
h1{font-size:34px;line-height:1.2;letter-spacing:-.035em;margin:6px 0}
.private{font-size:12px;border:1px solid var(--line);border-radius:99px;padding:7px 13px;white-space:nowrap;color:var(--muted)}
section{margin-bottom:20px;padding:28px 30px;border:1px solid var(--line);border-radius:18px;background:var(--paper);box-shadow:var(--shadow)}
.section-label{display:flex;align-items:center;gap:12px;margin-bottom:19px}
.section-label>span{font-size:12px;font-weight:700;color:var(--muted);border:1px solid var(--line);border-radius:7px;padding:2px 7px}
h2{font-size:20px;letter-spacing:-.02em;margin:0}
h3{font-size:17px;margin:0 0 7px;font-weight:650}
p{margin:7px 0}
.secondary,.timestamp{color:var(--muted);font-size:13px}
.timestamp{margin-top:15px}
.status-banner{display:flex;align-items:center;gap:12px;background:var(--soft);border-radius:11px;padding:15px 18px;margin-bottom:19px}
.status-banner h3{margin:0}
.status-dot{height:9px;width:9px;background:var(--accent);border-radius:50%;flex-shrink:0}
.warning .status-banner,.urgent .status-banner{background:var(--warn-soft)}
.warning .status-dot,.urgent .status-dot{background:var(--warn)}
.metric-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}
.metric+ .metric{border-left:1px solid var(--line);padding-left:25px}
.metric strong{display:block;font-size:38px;line-height:1.2;letter-spacing:-.045em;margin:10px 0}
.metric .pressure{text-transform:capitalize;font-size:33px}
.eyebrow{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.075em;font-weight:650}
.change-card{display:grid;grid-template-columns:1.15fr 1fr;gap:30px}
.change-card h3{font-size:25px;letter-spacing:-.03em;margin:9px 0}
.growers{list-style:none;margin:0;padding:0;border-left:1px solid var(--line);padding-left:24px}
.growers li{display:flex;justify-content:space-between;gap:16px;font-size:14px;border-bottom:1px solid var(--line);padding:11px 0}
.growers li:last-child{border:0}
.growers strong{font-size:13px;white-space:nowrap}
.table-scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;text-align:left;font-size:14px}
thead th{font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);font-weight:650;padding:12px 12px 12px 0;border-bottom:1px solid var(--line)}
tbody th,tbody td{border-bottom:1px solid var(--line);padding:16px 12px 16px 0;vertical-align:top}
tbody th{font-weight:550}
tbody tr:last-child th,tbody tr:last-child td{border:0}
td:not(:first-child){font-variant-numeric:tabular-nums}
.areas th:first-child{width:38%}
td small{display:block;font-size:11px;color:var(--muted);white-space:nowrap}
.size-bar{height:4px;border-radius:4px;background:var(--line);width:80%;max-width:200px;margin-top:9px;overflow:hidden}
.size-bar i{display:block;height:100%;background:var(--accent);opacity:.65}
.quality{display:block;font-size:11px;color:var(--warn)}
button{font:inherit;cursor:pointer}
.review{background:var(--paper);border:1px solid var(--line);color:var(--accent);padding:7px 10px;border-radius:8px;font-size:12px;font-weight:600;white-space:nowrap}
.review:hover{background:var(--soft);border-color:var(--accent)}
.review span{padding-left:4px}
.actions{list-style:none;padding:0;margin:0}
.actions li{display:flex;gap:16px;padding:17px 0;border-bottom:1px solid var(--line)}
.actions li:first-child{padding-top:0}
.actions li:last-child{border:0}
.action-number{flex-shrink:0;width:27px;height:27px;text-align:center;background:var(--soft);color:var(--accent);font-size:13px;font-weight:700;border-radius:50%;line-height:27px}
.actions p{font-size:14px;max-width:760px}
.actions .review{margin-top:6px}
.appendix-section{box-shadow:none}
summary{cursor:pointer;font-size:14px;line-height:1.8}
summary .secondary{display:block;margin-left:17px}
details[open]>summary{margin-bottom:20px}
details[open]>summary .secondary{display:none}
.appendix-content{border-top:1px solid var(--line);padding-top:20px}
.appendix-content label{display:block;font-size:13px;font-weight:650;margin-bottom:6px}
input[type=search]{width:100%;font:inherit;font-size:14px;padding:11px 13px;border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--ink)}
input:focus,button:focus-visible,summary:focus-visible{outline:3px solid #5ba99580;outline-offset:3px}
.folder-path{display:block;font:11px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere;min-width:240px;max-width:440px;color:var(--muted);margin:4px 0}
.copy{display:block;margin-top:3px;border:0;padding:2px 0;background:transparent;color:var(--accent);font-size:11px}
.coverage,.caveats,.nested ul{font-size:13px;padding-left:20px}
.coverage li,.caveats li{margin-bottom:9px}
code{font-size:11px;overflow-wrap:anywhere}
.appendix-content h3{margin-top:26px;font-size:15px}
.nested{border-top:1px solid var(--line);padding-top:16px;margin-top:20px}
textarea{width:100%;min-height:65px;font:12px ui-monospace,monospace;padding:9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
footer{font-size:12px;color:var(--muted);text-align:center;padding:6px 12px}
.empty{font-size:14px;padding:16px 0}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
[hidden]{display:none!important}

@media(prefers-color-scheme:dark){:root{--bg:#12171c;--paper:#1b232b;--ink:#edf1f6;--muted:#acbac8;--line:#33404b;--accent:#8acbb8;--soft:#213a35;--warn:#efbf79;--warn-soft:#3b3021;--shadow:none}
}

@media(max-width:680px){main{margin:20px auto;padding:0 14px 25px}
header{align-items:flex-start}
.private{font-size:10px;padding:5px 8px}
h1{font-size:27px}
section{padding:22px 18px;border-radius:14px}
.section-label{gap:9px}
h2{font-size:18px}
.metric-grid,.change-card{grid-template-columns:1fr;gap:18px}
.metric+.metric{border-left:0;border-top:1px solid var(--line);padding:18px 0 0}
.metric strong{font-size:34px}
.growers{border-left:0;border-top:1px solid var(--line);padding:6px 0 0}
.areas th:first-child{min-width:155px}
.areas td{min-width:95px}
.areas td:last-child{min-width:120px}
.actions li{gap:11px}
}

@media print{body{background:white;color:black}
main{max-width:none;margin:0}
section{box-shadow:none;break-inside:avoid}
.private,button{display:none}
details:not([open]){display:none}
}

.report-tools{display:flex;flex-direction:column;align-items:flex-end;gap:7px}
.unknown .status-banner{background:var(--bg)}
.unknown .status-dot{background:var(--muted)}
.growers small{display:block;color:var(--muted);font-size:11px}
"""

_SCRIPT = """
(() => {
  const appendix = document.getElementById('appendix');
  const input = document.getElementById('folder-filter');
  const rows = Array.from(document.querySelectorAll('#folder-table tbody tr'));
  const count = document.getElementById('filter-count');
  document.getElementById('reload-report').addEventListener('click', () => window.location.reload());
  function filter() {
    const query = input.value.trim().toLocaleLowerCase();
    let visible = 0;
    for (const row of rows) {
      row.hidden = !row.dataset.search.toLocaleLowerCase().includes(query);
      if (!row.hidden) visible++;
    }
    count.textContent = visible + ' of ' + rows.length + ' folders' + (visible ? '' : ' — try a shorter name or clear the filter');
  }
  input.addEventListener('input', filter);
  for (const button of document.querySelectorAll('[data-review]')) {
    button.addEventListener('click', () => {
      appendix.open = true;
      input.value = button.dataset.review;
      filter();
      appendix.scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start'});
      input.focus({preventScroll: true});
    });
  }
  for (const button of document.querySelectorAll('[data-copy]')) {
    button.addEventListener('click', async () => {
      const status = document.getElementById('copy-status');
      const fallback = document.getElementById('copy-fallback');
      try {
        if (!navigator.clipboard) throw new Error('Clipboard unavailable');
        await navigator.clipboard.writeText(button.dataset.copy);
        fallback.hidden = true;
        status.textContent = 'Folder path copied.';
      } catch (_) {
        fallback.value = button.dataset.copy;
        fallback.hidden = false;
        fallback.focus();
        fallback.select();
        status.textContent = 'Path selected. Press Command+C on Mac, or Ctrl+C, to copy.';
      }
    });
  }
})();
"""


def render(payload: dict) -> str:
    """Render local metadata as inert text, without external assets or a server."""
    view = build(payload)
    severity = view.get("status", {}).get("severity", "unknown")
    tone = severity if severity in {"warning", "urgent", "unknown"} else "normal"
    content = _status(view) + _changes(view) + _areas(view) + _actions(view) + _appendix(view)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer"><title>Storage &amp; memory — Agent Storage Guard</title><style>{_STYLE}</style></head>
<body class="{tone}"><main><header><div><div class="brand">Agent Storage Guard</div><h1>Storage &amp; memory</h1></div>
<div class="report-tools"><span class="private">Local report · read only</span><button class="review" id="reload-report" type="button">Reload saved report</button></div></header>{content}
<footer>Bookmark this page for easy access. Reload shows the latest saved measurements.</footer>
</main><script>{_SCRIPT}</script></body></html>'''
