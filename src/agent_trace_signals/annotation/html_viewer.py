"""Generate a self-contained HTML annotation viewer for a session."""

from __future__ import annotations
import json
from pathlib import Path


# Entity-type → CSS colour
_TYPE_COLORS = {
    "file":       "#3b82f6",   # blue
    "commit":     "#8b5cf6",   # purple
    "technology": "#10b981",   # green
    "concept":    "#f59e0b",   # amber
    "tool":       "#06b6d4",   # cyan
    "person":     "#ec4899",   # pink
    "org":        "#f97316",   # orange
    "pr":         "#6366f1",   # indigo
    "model":      "#14b8a6",   # teal
    "dataset":    "#84cc16",   # lime
}
_DEFAULT_COLOR = "#94a3b8"


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def _highlight_chunk(chunk_text: str, spans: list[dict]) -> str:
    """Return HTML with entity spans highlighted. spans = [{start, end, name, type, eid}]."""
    # Sort by start, resolve overlaps by keeping higher-priority (shorter) span
    spans = sorted(spans, key=lambda s: (s["start"], -(s["end"] - s["start"])))
    merged: list[dict] = []
    for s in spans:
        if merged and s["start"] < merged[-1]["end"]:
            continue  # skip overlapping
        merged.append(s)

    result = []
    pos = 0
    for s in merged:
        if s["start"] > pos:
            result.append(_escape(chunk_text[pos:s["start"]]))
        color = _TYPE_COLORS.get(s["type"], _DEFAULT_COLOR)
        label = _escape(s["name"])
        etype = _escape(s["type"])
        eid = _escape(s["eid"])
        snippet = _escape(chunk_text[s["start"]:s["end"]])
        result.append(
            f'<mark class="ent" data-eid="{eid}" data-type="{etype}" '
            f'style="background:{color}22;border-bottom:2px solid {color};'
            f'cursor:pointer;" title="{etype}: {label}">{snippet}</mark>'
        )
        pos = s["end"]
    if pos < len(chunk_text):
        result.append(_escape(chunk_text[pos:]))
    return "".join(result)


def _find_spans_in_chunk(chunk_text: str, entities: list[dict]) -> list[dict]:
    """Locate each entity's canonical_name in the chunk text (case-insensitive)."""
    spans = []
    lower = chunk_text.lower()
    for e in entities:
        name = e["canonical_name"]
        idx = lower.find(name.lower())
        if idx == -1:
            continue
        spans.append({
            "start": idx,
            "end": idx + len(name),
            "name": name,
            "type": e["entity_type"],
            "eid": e["id"],
        })
    return spans


def generate(
    session_id: str,
    chunks: list[dict],         # [{chunk_index, chunk_text}]
    entities: list[dict],       # [{id, canonical_name, entity_type}]
    occurrences: list[dict],    # [{record_id, entity_id, role, span_start, span_end}]
    annotation: dict,           # the annotation JSON (expected_entities etc.)
    out_path: Path,
) -> None:
    """Write a self-contained HTML annotation viewer to out_path."""

    # Build entity lookup and per-chunk span index
    entity_by_id = {e["id"]: e for e in entities}
    # Map record_id → chunk_index via chunk list (record_id encodes session+chunk_index)

    # For each chunk, collect entity spans (prefer DB spans, fallback to text search)
    chunk_spans: dict[int, list[dict]] = {c["chunk_index"]: [] for c in chunks}

    # Group occurrences by record
    from collections import defaultdict
    occ_by_record: dict[str, list[dict]] = defaultdict(list)
    for occ in occurrences:
        occ_by_record[occ["record_id"]].append(occ)

    # Assign spans per chunk
    for chunk in chunks:
        ci = chunk["chunk_index"]
        text = chunk["chunk_text"]
        chunk_entities = []
        for occ in occ_by_record.get(_record_id_for_chunk(session_id, ci), []):
            ent = entity_by_id.get(occ["entity_id"])
            if ent:
                chunk_entities.append(ent)
        if chunk_entities:
            chunk_spans[ci] = _find_spans_in_chunk(text, chunk_entities)

    # Build annotation state: set of expected entity ids/names
    ann_names = {
        (e["entity_type"], e["canonical_name"].lower())
        for e in annotation.get("expected_entities", [])
    }
    extracted_names = {
        (e["entity_type"], e["canonical_name"].lower())
        for e in entities
    }
    fn_names = ann_names - extracted_names   # in annotation but not extracted

    # -----------------------------------------------------------------------
    # Render HTML
    # -----------------------------------------------------------------------
    entity_rows_html = []
    for e in sorted(entities, key=lambda x: (x["entity_type"], x["canonical_name"])):
        color = _TYPE_COLORS.get(e["entity_type"], _DEFAULT_COLOR)
        key = (e["entity_type"], e["canonical_name"].lower())
        is_fp = key not in ann_names and ann_names  # mark as FP only if annotation exists
        row_class = "fp-candidate" if is_fp else "keep"
        entity_rows_html.append(
            f'<tr class="ent-row {row_class}" data-eid="{_escape(e["id"])}" '
            f'data-name="{_escape(e["canonical_name"])}" data-type="{_escape(e["entity_type"])}">'
            f'<td><span class="badge" style="background:{color}22;color:{color};'
            f'border:1px solid {color};">{_escape(e["entity_type"])}</span></td>'
            f'<td class="ent-name">{_escape(e["canonical_name"])}</td>'
            f'<td><button class="fp-btn" onclick="toggleFP(this)">FP ✕</button></td>'
            f'</tr>'
        )

    chunks_html = []
    for chunk in sorted(chunks, key=lambda c: c["chunk_index"]):
        ci = chunk["chunk_index"]
        highlighted = _highlight_chunk(chunk["chunk_text"], chunk_spans.get(ci, []))
        chunks_html.append(
            f'<div class="chunk" id="chunk-{ci}">'
            f'<div class="chunk-header">chunk {ci}</div>'
            f'<pre class="chunk-text">{highlighted}</pre>'
            f'</div>'
        )

    fn_rows_html = []
    for (etype, ename) in sorted(fn_names):
        fn_rows_html.append(
            f'<tr class="fn-row"><td>{_escape(etype)}</td>'
            f'<td>{_escape(ename)}</td>'
            f'<td><em>(in annotation, not extracted)</em></td></tr>'
        )

    sid_short = session_id[:16]
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Annotation — {sid_short}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: system-ui, sans-serif; font-size: 13px; background: #0f172a; color: #e2e8f0; display: flex; height: 100vh; overflow: hidden; }}
  #sidebar {{ width: 360px; min-width: 280px; background: #1e293b; border-right: 1px solid #334155; display: flex; flex-direction: column; overflow: hidden; }}
  #main {{ flex: 1; overflow-y: auto; padding: 16px; }}
  h2 {{ font-size: 14px; color: #94a3b8; padding: 12px 16px 8px; border-bottom: 1px solid #334155; }}
  .entity-table {{ width: 100%; border-collapse: collapse; }}
  .entity-table th {{ text-align: left; padding: 6px 8px; font-size: 11px; color: #64748b; border-bottom: 1px solid #1e293b; }}
  .ent-row {{ border-bottom: 1px solid #1e2d40; }}
  .ent-row:hover {{ background: #263548; }}
  .ent-row td {{ padding: 4px 8px; vertical-align: middle; }}
  .ent-row.fp-candidate {{ opacity: 0.5; }}
  .ent-row.fp-candidate .ent-name {{ text-decoration: line-through; }}
  .ent-row.removed {{ display: none; }}
  .badge {{ font-size: 10px; padding: 1px 5px; border-radius: 3px; white-space: nowrap; }}
  .fp-btn {{ font-size: 10px; padding: 2px 6px; border: 1px solid #475569; border-radius: 3px; background: transparent; color: #f87171; cursor: pointer; }}
  .fp-btn:hover {{ background: #7f1d1d44; }}
  .fp-btn.undone {{ color: #86efac; border-color: #86efac; }}
  .chunk {{ background: #1e293b; border-radius: 6px; margin-bottom: 12px; overflow: hidden; }}
  .chunk-header {{ font-size: 10px; color: #64748b; padding: 4px 10px; background: #0f172a; }}
  .chunk-text {{ padding: 10px; white-space: pre-wrap; word-break: break-word; line-height: 1.6; font-family: "SF Mono", "Fira Code", monospace; font-size: 12px; color: #cbd5e1; max-height: 400px; overflow-y: auto; }}
  mark.ent {{ border-radius: 2px; padding: 0 1px; }}
  #fn-panel {{ padding: 10px 12px; border-top: 1px solid #334155; }}
  #fn-panel h3 {{ font-size: 12px; color: #94a3b8; margin-bottom: 8px; }}
  .fn-form {{ display: flex; flex-direction: column; gap: 6px; }}
  .fn-form input, .fn-form select {{ background: #0f172a; border: 1px solid #334155; color: #e2e8f0; padding: 4px 8px; border-radius: 4px; font-size: 12px; }}
  .fn-form button {{ background: #1d4ed8; color: white; border: none; padding: 5px 10px; border-radius: 4px; cursor: pointer; font-size: 12px; }}
  .fn-form button:hover {{ background: #2563eb; }}
  #save-btn {{ margin: 8px 12px; padding: 7px; background: #065f46; border: none; color: #d1fae5; border-radius: 5px; cursor: pointer; font-size: 12px; width: calc(100% - 24px); }}
  #save-btn:hover {{ background: #047857; }}
  #status {{ font-size: 11px; color: #64748b; padding: 4px 12px 8px; }}
  .fn-table {{ width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 8px; }}
  .fn-table td {{ padding: 3px 6px; color: #fbbf24; }}
  .sidebar-scroll {{ overflow-y: auto; flex: 1; }}
</style>
</head>
<body>

<div id="sidebar">
  <h2>Extracted entities ({len(entities)}) — {sid_short}</h2>
  <div class="sidebar-scroll">
    <table class="entity-table">
      <thead><tr><th>Type</th><th>Name</th><th></th></tr></thead>
      <tbody id="entity-body">
        {"".join(entity_rows_html)}
      </tbody>
    </table>
    {"<table class='fn-table'><thead><tr><th>Type</th><th>Name</th><th>Note</th></tr></thead><tbody>" + "".join(fn_rows_html) + "</tbody></table>" if fn_rows_html else ""}
  </div>

  <div id="fn-panel">
    <h3>Add missed entity (FN)</h3>
    <div class="fn-form">
      <select id="fn-type">
        <option value="file">file</option>
        <option value="technology" selected>technology</option>
        <option value="concept">concept</option>
        <option value="tool">tool</option>
        <option value="commit">commit</option>
        <option value="pr">pr</option>
        <option value="person">person</option>
        <option value="org">org</option>
        <option value="model">model</option>
        <option value="dataset">dataset</option>
      </select>
      <input id="fn-name" type="text" placeholder="canonical name">
      <input id="fn-role" type="text" placeholder="role (e.g. tool_used, file_modified)">
      <button onclick="addFN()">Add FN</button>
    </div>
  </div>

  <button id="save-btn" onclick="saveAnnotation()">Save annotation JSON</button>
  <div id="status"></div>
</div>

<div id="main">
  {"".join(chunks_html)}
</div>

<script>
const SESSION_ID = {json.dumps(session_id)};
const ANNOTATION_PATH = {json.dumps(str(out_path.with_suffix(".json")))};

// State: track FP removals and FN additions
let fpRemoved = new Set();   // entity ids marked as FP
let fnAdded = [];            // {{name, type, role}}

// Pre-mark fp-candidates (not in annotation) as removed
document.querySelectorAll('.ent-row.fp-candidate').forEach(row => {{
  // start un-removed — user decides
}});

function toggleFP(btn) {{
  const row = btn.closest('.ent-row');
  const eid = row.dataset.eid;
  if (fpRemoved.has(eid)) {{
    fpRemoved.delete(eid);
    row.classList.remove('removed');
    row.classList.add('fp-candidate');
    btn.textContent = 'FP ✕';
    btn.classList.remove('undone');
  }} else {{
    fpRemoved.add(eid);
    row.classList.remove('fp-candidate');
    row.classList.add('removed');
    btn.textContent = '↩ undo';
    btn.classList.add('undone');
  }}
  // Dim matching marks in chunks
  document.querySelectorAll(`mark[data-eid="${{eid}}"]`).forEach(m => {{
    m.style.opacity = fpRemoved.has(eid) ? '0.2' : '1';
  }});
}}

function addFN() {{
  const name = document.getElementById('fn-name').value.trim();
  const type = document.getElementById('fn-type').value;
  const role = document.getElementById('fn-role').value.trim();
  if (!name) {{ alert('Enter a canonical name'); return; }}
  fnAdded.push({{name, type, role}});
  document.getElementById('fn-name').value = '';
  document.getElementById('fn-role').value = '';
  document.getElementById('status').textContent = `FN added: ${{type}}:${{name}} — save to persist`;
}}

async function saveAnnotation() {{
  // Build diff: entities to keep (not FP-removed), FNs to add
  const keepRows = [...document.querySelectorAll('.ent-row:not(.removed)')];
  const kept = keepRows.map(r => ({{
    canonical_name: r.dataset.name,
    entity_type: r.dataset.type,
  }}));
  // Merge with fnAdded
  const fnEntities = fnAdded.map(f => ({{
    canonical_name: f.name,
    entity_type: f.type,
  }}));
  const fnOccurrences = fnAdded.filter(f => f.role).map(f => ({{
    entity_canonical: f.name,
    role: f.role,
  }}));
  const payload = {{
    session_id: SESSION_ID,
    kept_entity_ids: [...keepRows.map(r => r.dataset.eid)],
    fn_entities: fnEntities,
    fn_occurrences: fnOccurrences,
  }};
  // Since we can't write files from JS, emit a download
  const annotation = buildAnnotationJSON(kept.concat(fnEntities), fnOccurrences);
  const blob = new Blob([JSON.stringify(annotation, null, 2)], {{type: 'application/json'}});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = ANNOTATION_PATH.split('/').pop();
  a.click();
  document.getElementById('status').textContent = 'Downloaded — replace the annotation JSON file with this download.';
}}

function buildAnnotationJSON(entities, fnOccurrences) {{
  // Rebuild expected_occurrences from kept entities + FN occurrences
  const baseAnnotation = {json.dumps(annotation, indent=2)};
  // Filter expected_entities to kept ones + FNs
  baseAnnotation.expected_entities = entities;
  // Keep occurrence entries whose entity_canonical is in kept set
  const keptNames = new Set(entities.map(e => e.canonical_name.toLowerCase()));
  baseAnnotation.expected_occurrences = (baseAnnotation.expected_occurrences || [])
    .filter(o => keptNames.has(o.entity_canonical.toLowerCase()))
    .concat(fnOccurrences);
  return baseAnnotation;
}}
</script>
</body>
</html>"""

    out_path.write_text(html, encoding="utf-8")


def _record_id_for_chunk(session_id: str, chunk_index: int) -> str:
    import hashlib
    return hashlib.sha256(f"{session_id}:{chunk_index}".encode()).hexdigest()
