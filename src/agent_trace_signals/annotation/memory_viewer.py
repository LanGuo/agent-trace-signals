"""Generate a self-contained HTML annotation viewer for frequency memories."""

from __future__ import annotations
import json


_TYPE_COLORS = {
    "procedural": "#10b981",   # green
    "preference": "#06b6d4",   # cyan
    "episodic":   "#f59e0b",   # amber
}
_DEFAULT_COLOR = "#94a3b8"


def _escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def generate(memories: list[dict], existing_annotation: dict) -> str:
    """Return a self-contained HTML string for labeling frequency memories.

    memories: output of store.get_frequency_memories_for_annotation()
    existing_annotation: parsed level1a.json dict (may be empty dict)
    """
    if not memories:
        return "<html><body><p>No frequency memories found. Run <code>ats analytics light</code> first.</p></body></html>"

    # Build initial labels dict from existing annotation
    initial_labels: dict[str, dict] = {}
    for lm in existing_annotation.get("labeled_memories", []):
        initial_labels[lm["memory_id"]] = lm

    # Group memories by entity_name
    groups: dict[str, list[dict]] = {}
    for m in memories:
        key = m.get("entity_name") or "(unknown entity)"
        groups.setdefault(key, []).append(m)

    # Build entity sections HTML
    entity_sections = []
    for entity_name, mems in groups.items():
        first = mems[0]
        etype = _escape(first.get("entity_type") or "")
        profile = _escape(first.get("entity_profile") or "")
        degree = first.get("degree_count") or 0

        rows_html = []
        for m in mems:
            mid = _escape(m["memory_id"])
            content = _escape(m["content"])
            mtype = m.get("memory_type", "")
            color = _TYPE_COLORS.get(mtype, _DEFAULT_COLOR)
            ev = m.get("evidence_count", 1)
            type_badge = (
                f'<span style="background:{color}22;border:1px solid {color};'
                f'border-radius:4px;padding:1px 6px;font-size:11px;color:{color}">'
                f'{_escape(mtype)}</span>'
            )
            evidence_badge = f'<span style="color:#94a3b8;font-size:11px">ev={ev}</span>'

            rows_html.append(
                f'<div class="memory-row" data-memory-id="{mid}"'
                f' data-entity="{_escape(entity_name)}"'
                f' data-entity-type="{etype}"'
                f' data-memory-type="{_escape(mtype)}"'
                f' data-content="{content}">'
                f'<div class="memory-content">{content}</div>'
                f'<div class="memory-meta">{type_badge} {evidence_badge}</div>'
                f'<div class="label-buttons">'
                f'<button class="lbl-btn" data-label="correct"'
                f' onclick="setLabel(\'{mid}\',\'correct\',this)">&#10003; Correct</button>'
                f'<button class="lbl-btn" data-label="incorrect"'
                f' onclick="setLabel(\'{mid}\',\'incorrect\',this)">&#10007; Incorrect</button>'
                f'<button class="lbl-btn" data-label="partial"'
                f' onclick="setLabel(\'{mid}\',\'partial\',this)">&#x7e; Partial</button>'
                f'</div></div>'
            )

        profile_html = f'<div class="entity-profile">{profile}</div>' if profile else ""
        entity_sections.append(
            f'<div class="entity-section">'
            f'<div class="entity-header" onclick="toggleSection(this)">'
            f'<span class="entity-name">{_escape(entity_name)}</span>'
            f'<span class="entity-meta">{etype} &bull; degree={degree} &bull; {len(mems)} memories</span>'
            f'<span class="toggle-arrow">&#9660;</span>'
            f'</div>'
            f'{profile_html}'
            f'<div class="entity-memories">{"".join(rows_html)}</div>'
            f'</div>'
        )

    initial_labels_json = json.dumps(initial_labels).replace("</script>", "<\\/script>")
    memories_meta_json = json.dumps([
        {
            "memory_id": m["memory_id"],
            "entity_name": m.get("entity_name") or "",
            "entity_type": m.get("entity_type") or "",
            "memory_type": m.get("memory_type") or "",
            "content": m["content"],
        }
        for m in memories
    ]).replace("</script>", "<\\/script>")
    expected_memories_json = json.dumps(existing_annotation.get("expected_memories", [])).replace("</script>", "<\\/script>")
    total = len(memories)

    css = """
  body { font-family: system-ui, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; background: #0f172a; color: #e2e8f0; }
  h1 { color: #f1f5f9; font-size: 20px; margin-bottom: 4px; }
  .subtitle { color: #94a3b8; font-size: 13px; margin-bottom: 24px; }
  .entity-section { border: 1px solid #1e293b; border-radius: 8px; margin-bottom: 12px; overflow: hidden; }
  .entity-header { display: flex; align-items: center; gap: 12px; padding: 10px 14px; background: #1e293b; cursor: pointer; user-select: none; }
  .entity-name { font-weight: 600; font-size: 15px; color: #f1f5f9; flex: 1; }
  .entity-meta { font-size: 12px; color: #64748b; }
  .toggle-arrow { color: #64748b; font-size: 12px; transition: transform 0.2s; }
  .entity-header.collapsed .toggle-arrow { transform: rotate(-90deg); }
  .entity-profile { padding: 8px 14px; font-size: 12px; color: #94a3b8; background: #0f172a; border-bottom: 1px solid #1e293b; font-style: italic; }
  .entity-memories { padding: 8px; display: flex; flex-direction: column; gap: 8px; }
  .entity-header.collapsed + .entity-profile,
  .entity-header.collapsed ~ .entity-memories { display: none; }
  .memory-row { background: #1e293b; border-radius: 6px; padding: 10px 12px; border-left: 3px solid #334155; }
  .memory-row.labeled-correct { border-left-color: #10b981; }
  .memory-row.labeled-incorrect { border-left-color: #ef4444; }
  .memory-row.labeled-partial { border-left-color: #f59e0b; }
  .memory-content { font-size: 13px; color: #cbd5e1; margin-bottom: 6px; line-height: 1.5; }
  .memory-meta { display: flex; gap: 8px; margin-bottom: 8px; }
  .label-buttons { display: flex; gap: 6px; }
  .lbl-btn { padding: 3px 10px; border: 1px solid #334155; background: #0f172a; color: #94a3b8; border-radius: 4px; cursor: pointer; font-size: 12px; }
  .lbl-btn:hover { background: #334155; color: #e2e8f0; }
  .lbl-btn.active-correct { background: #10b98122; border-color: #10b981; color: #10b981; }
  .lbl-btn.active-incorrect { background: #ef444422; border-color: #ef4444; color: #ef4444; }
  .lbl-btn.active-partial { background: #f59e0b22; border-color: #f59e0b; color: #f59e0b; }
  .save-bar { position: sticky; bottom: 0; background: #0f172a; border-top: 1px solid #1e293b; padding: 12px 0; display: flex; align-items: center; gap: 16px; }
  .save-btn { padding: 8px 20px; background: #3b82f6; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; }
  .save-btn:hover { background: #2563eb; }
  .status { font-size: 13px; color: #94a3b8; }
"""

    js = f"""
const INITIAL_LABELS = {initial_labels_json};
const MEMORIES_META = {memories_meta_json};
const EXPECTED_MEMORIES = {expected_memories_json};
const labels = {{}};
Object.assign(labels, INITIAL_LABELS);

function setLabel(memoryId, label, btn) {{
  if (labels[memoryId] && labels[memoryId].label === label) {{
    delete labels[memoryId];
    btn.closest('.memory-row').className = 'memory-row';
    btn.closest('.label-buttons').querySelectorAll('.lbl-btn').forEach(b => b.className = 'lbl-btn');
  }} else {{
    labels[memoryId] = {{ ...MEMORIES_META.find(m => m.memory_id === memoryId), label }};
    const row = btn.closest('.memory-row');
    row.className = 'memory-row labeled-' + label;
    btn.closest('.label-buttons').querySelectorAll('.lbl-btn').forEach(b => {{ b.className = 'lbl-btn'; }});
    btn.className = 'lbl-btn active-' + label;
  }}
  updateStatus();
}}

function updateStatus() {{
  const n = Object.keys(labels).length;
  document.getElementById('status').textContent = n + ' of {total} memories labeled';
}}

function toggleSection(header) {{
  header.classList.toggle('collapsed');
}}

function saveAnnotation() {{
  const data = {{
    annotation_type: "level1a",
    notes: "Generated by ats annotate-memories",
    labeled_memories: Object.values(labels)
  }};
  if (EXPECTED_MEMORIES.length > 0) {{
    data.expected_memories = EXPECTED_MEMORIES;
  }}
  const blob = new Blob([JSON.stringify(data, null, 2)], {{type: 'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'level1a.json';
  a.click();
}}

document.addEventListener('DOMContentLoaded', () => {{
  Object.entries(INITIAL_LABELS).forEach(([memId, lm]) => {{
    const row = document.querySelector('[data-memory-id="' + memId + '"]');
    if (!row) return;
    row.className = 'memory-row labeled-' + lm.label;
    const btn = row.querySelector('[data-label="' + lm.label + '"]');
    if (btn) btn.className = 'lbl-btn active-' + lm.label;
  }});
  updateStatus();
}});
"""

    sections_html = "\n".join(entity_sections)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Frequency Memory Annotation</title>
<style>{css}</style>
</head>
<body>
<h1>Frequency Memory Annotation</h1>
<p class="subtitle">Label each memory as Correct, Incorrect, or Partial. Click Save to download the updated annotation file.</p>
{sections_html}
<div class="save-bar">
  <button class="save-btn" onclick="saveAnnotation()">Save annotations/level1a.json</button>
  <span class="status" id="status">0 of {total} memories labeled</span>
</div>
<script>{js}</script>
</body>
</html>"""
