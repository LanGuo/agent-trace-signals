"""Generate the D-prompt extraction audit HTML artifact from collected JSON data."""
import json
import html as htmllib
from pathlib import Path

SCRATCH = str(Path(__file__).resolve().parent)

data = json.load(open(f"{SCRATCH}/new_chunks_results.json"))
chunks_meta = json.load(open(f"{SCRATCH}/chunks_new.json"))

SESSION_DISPLAY = {
    "pref_flag_naming": ("ATS — --use-d-prompt flag naming discussion", "claude_code", "workspace: agent-trace-signals"),
    "pref_doc_naming": ("ATS — phase-review doc naming discussion", "claude_code", "workspace: agent-trace-signals"),
    "open_q_1": ("ATS — inflection detector evaluation", "claude_code", "workspace: agent-trace-signals"),
    "open_q_2": ("ATS — critical-step scoring re-run", "claude_code", "workspace: agent-trace-signals"),
}

MODEL_ORDER = ["gemma3:12b", "gemma4:e4b"]
MODEL_DISPLAY = {
    "gemma3:12b": ("gemma3:12b", "user's preferred model overall"),
    "gemma4:e4b": ("gemma4:e4b", "only model that caught the preference statements"),
}

def esc(s):
    return htmllib.escape(s or "")

def entity_chips(entities):
    if not entities:
        return '<p class="empty">no entities</p>'
    chips = []
    for e in entities:
        name = e.get("name") or e.get("canonical_name", "")
        etype = e.get("type") or e.get("entity_type", "")
        role = e.get("role", "")
        role_html = f'<span class="chip-role">{esc(role)}</span>' if role else ""
        chips.append(f'<span class="chip" data-type="{esc(etype)}"><span class="chip-name">{esc(name)}</span><span class="chip-type">{esc(etype)}</span>{role_html}</span>')
    return '<div class="chips">' + "".join(chips) + '</div>'

def memory_list(memories):
    if not memories:
        return '<p class="empty">no memories</p>'
    items = []
    for m in memories:
        mtype = m.get("type") or m.get("memory_type", "")
        status = m.get("status", "")
        content = m.get("content", "")
        status_html = f'<span class="status-badge" data-status="{esc(status)}">{esc(status)}</span>' if status else ""
        items.append(f'<li><span class="tag" data-mtype="{esc(mtype)}">{esc(mtype)}</span>{status_html}{esc(content)}</li>')
    return '<ul class="mem-list">' + "".join(items) + '</ul>'

def pattern_list(patterns):
    if not patterns:
        return '<p class="empty">no patterns</p>'
    items = []
    for p in patterns:
        ptype = p.get("type", "")
        content = p.get("content", "")
        items.append(f'<li><span class="tag" data-ptype="{esc(ptype)}">{esc(ptype)}</span>{esc(content)}</li>')
    return '<ul class="mem-list pattern-list">' + "".join(items) + '</ul>'

def preference_list(preferences):
    if not preferences:
        return '<p class="empty">no preferences</p>'
    items = []
    for p in preferences:
        content = p.get("content", "")
        items.append(f'<li>{esc(content)}</li>')
    return '<ul class="mem-list pref-list">' + "".join(items) + '</ul>'

by_chunk = {}
for r in data:
    key = (r["chunk_label"], r["chunk_index"])
    by_chunk.setdefault(key, {})[r["model"]] = r

chunk_order = [
    ("pref_flag_naming", 15), ("pref_doc_naming", 11),
    ("open_q_1", 24), ("open_q_2", 47),
]

sections_html = []
for label, idx in chunk_order:
    title, harness, ws = SESSION_DISPLAY[label]
    anchor = f"{label}-{idx}"
    models = by_chunk[(label, idx)]

    cols = []
    for model_key in MODEL_ORDER:
        r = models.get(model_key)
        mname, msub = MODEL_DISPLAY[model_key]
        if not r:
            cols.append(f'<div class="col"><div class="col-head"><h4>{esc(mname)}</h4><p>{esc(msub)}</p></div><p class="empty">not available</p></div>')
            continue
        n_e, n_m, n_p, n_pref = len(r.get("entities", [])), len(r.get("memories", [])), len(r.get("patterns", [])), len(r.get("preferences", []))
        cols.append(f'''<div class="col">
          <div class="col-head">
            <h4>{esc(mname)}</h4>
            <p>{esc(msub)}</p>
            <div class="col-counts"><span>{n_e} ent</span><span>{n_m} mem</span><span>{n_p} pat</span><span>{n_pref} pref</span></div>
          </div>
          <div class="field"><span class="field-label">Summary</span><p class="summary-text">{esc(r.get("summary",""))}</p></div>
          <div class="field"><span class="field-label">Entities</span>{entity_chips(r.get("entities",[]))}</div>
          <div class="field"><span class="field-label">Memories</span>{memory_list(r.get("memories",[]))}</div>
          <div class="field"><span class="field-label">Patterns</span>{pattern_list(r.get("patterns",[]))}</div>
          <div class="field"><span class="field-label">Preferences</span>{preference_list(r.get("preferences",[]))}</div>
        </div>''')

    sections_html.append(f'''
    <section class="chunk-block" id="{anchor}">
      <div class="chunk-head">
        <span class="eyebrow">{esc(harness)}</span>
        <h3>{esc(title)} <span class="chunk-idx">chunk {idx}</span></h3>
        <p class="chunk-ws">{esc(ws)}</p>
      </div>
      <div class="col-scroll"><div class="col-grid">
        {"".join(cols)}
      </div></div>
    </section>''')

per_chunk_sections = "\n".join(sections_html)

NAV_SHORT = {"pref_flag_naming": "Flag naming", "pref_doc_naming": "Doc naming", "open_q_1": "Open Q 1", "open_q_2": "Open Q 2"}
nav_links = "\n".join(
    f'<a href="#{label}-{idx}">{NAV_SHORT[label]} · c{idx}</a>'
    for label, idx in chunk_order
)

print(len(per_chunk_sections), "chars of per-chunk HTML generated")
open(f"{SCRATCH}/_new_per_chunk_sections.html", "w").write(per_chunk_sections)
open(f"{SCRATCH}/_new_nav_links.html", "w").write(nav_links)
print("done")
