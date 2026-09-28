"""Generate a self-contained HTML labeling viewer for failure candidates."""

from __future__ import annotations
import json


_INFLECTION_COLORS = {
    "escalation":      "#ef4444",
    "loop_entry":      "#f97316",
    "agent_thrash":    "#eab308",
    "user_correction": "#8b5cf6",
    "strategy_pivot":  "#06b6d4",
    "loop_exit":       "#10b981",
}
_DEFAULT_COLOR = "#94a3b8"

_TAG_STYLES = {
    "PRECIPITATING":          "background:#1e3a5f;color:#93c5fd;",
    "INFLECTION":             "background:#4c1d1d;color:#fca5a5;",
    "CORRECTION":             "background:#14532d;color:#86efac;",
    "CONTINUATION_BOUNDARY":  "background:#3b2f00;color:#fde68a;",
}


def _e(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _exchange_html(ex: dict) -> str:
    tags_html = ""
    for tag in ex.get("tags", []):
        style = _TAG_STYLES.get(tag, "background:#333;color:#fff;")
        tags_html += f'<span style="font-size:11px;padding:2px 7px;border-radius:3px;margin-right:4px;{style}">{tag}</span>'

    if ex.get("is_wakeup"):
        tags_html += '<span style="font-size:11px;padding:2px 7px;border-radius:3px;margin-right:4px;background:#1a1a3e;color:#a5b4fc;">⏰ WAKEUP INJECTION</span>'

    user = _e(ex.get("user", ""))
    tools_html = ""
    for t in ex.get("tools", [])[:8]:
        tools_html += f'<code style="display:inline-block;background:#1e293b;color:#94a3b8;font-size:11px;padding:1px 6px;border-radius:3px;margin:2px 2px 0 0;">{_e(t)}</code>'
    errors_html = ""
    for err in ex.get("errors", []):
        errors_html += f'<div style="margin-top:4px;padding:4px 8px;background:#2d0f0f;color:#fca5a5;font-size:11px;font-family:monospace;border-radius:3px;white-space:pre-wrap;">{_e(err[:300])}</div>'

    bg = "#1a1f2e" if not ex.get("tags") else "#1e2030"
    return f"""
<div style="margin:6px 0;padding:10px 12px;background:{bg};border-radius:6px;border-left:3px solid {'#475569' if not ex.get('tags') else '#64748b'};">
  <div style="margin-bottom:6px;display:flex;align-items:center;gap:8px;">
    <span style="color:#64748b;font-size:12px;">exchange {ex['exchange_idx']}</span>
    {tags_html}
  </div>
  <div style="color:#e2e8f0;font-size:13px;margin-bottom:6px;white-space:pre-wrap;">{user}</div>
  <div>{tools_html}</div>
  {errors_html}
</div>"""


def generate(records: list[dict]) -> str:
    cards = []
    for i, r in enumerate(records):
        color = _INFLECTION_COLORS.get(r["inflection_type"], _DEFAULT_COLOR)
        exchanges_html = "".join(_exchange_html(ex) for ex in r.get("window", []))

        signal_detail = r.get("signal_detail", {})
        metrics_html = " &nbsp;·&nbsp; ".join(
            f'<span style="color:#94a3b8;">{k}:</span> <span style="color:#e2e8f0;">{round(v,2)}</span>'
            for k, v in signal_detail.items()
            if k in ("action_entropy", "action_target_recurrence", "error_rate", "read_only_ratio")
        )

        cont_warning = ""
        if r.get("correction_is_continuation"):
            cont_warning += '<div style="margin:6px 0;padding:6px 10px;background:#3b2f00;color:#fde68a;font-size:12px;border-radius:4px;">⚠ Correction turn is a context-continuation boundary — ground truth may be unreliable</div>'

        inflection_ex = next((ex for ex in r.get("window", []) if "INFLECTION" in ex.get("tags", [])), None)
        if inflection_ex and inflection_ex.get("is_wakeup"):
            cont_warning += '<div style="margin:6px 0;padding:6px 10px;background:#1a1a3e;color:#a5b4fc;font-size:12px;border-radius:4px;">⏰ Inflection turn is a ScheduleWakeup injection — likely a monitoring loop, not a genuine agent failure</div>'

        cards.append(f"""
<div id="card-{i}" data-idx="{i}" style="margin:0 0 28px 0;padding:20px;background:#0f1117;border:1px solid #1e293b;border-radius:10px;">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;">
    <div>
      <span style="font-size:15px;font-weight:600;color:{color};">{_e(r['inflection_type'])}</span>
      <span style="margin-left:10px;color:#64748b;font-size:13px;">feature: {_e(r['dominant_feature'])}</span>
      <span style="margin-left:10px;color:#64748b;font-size:13px;">strength: {r['signal_strength']}</span>
    </div>
    <div id="status-{i}" style="font-size:13px;color:#64748b;">unlabeled</div>
  </div>

  <div style="margin-bottom:10px;font-size:12px;">{metrics_html}</div>
  {cont_warning}

  <div style="margin:10px 0 14px 0;">{exchanges_html}</div>

  <!-- Label controls -->
  <div style="padding:14px;background:#0d1117;border:1px solid #1e293b;border-radius:8px;">

    <div style="margin-bottom:12px;">
      <div style="color:#94a3b8;font-size:12px;margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em;">Genuine failure?</div>
      <label style="cursor:pointer;margin-right:20px;">
        <input type="radio" name="genuine-{i}" value="true" onchange="update({i})" {'checked' if r.get('genuine') is True else ''}>
        <span style="color:#86efac;margin-left:4px;">Yes</span>
      </label>
      <label style="cursor:pointer;margin-right:20px;">
        <input type="radio" name="genuine-{i}" value="false" onchange="update({i})" {'checked' if r.get('genuine') is False else ''}>
        <span style="color:#fca5a5;margin-left:4px;">No — false positive</span>
      </label>
    </div>

    <div style="margin-bottom:12px;">
      <div style="color:#94a3b8;font-size:12px;margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em;">Recovery type</div>
      {''.join(
        f'<label style="cursor:pointer;margin-right:16px;display:inline-block;margin-bottom:4px;"><input type="radio" name="recovery-{i}" value="{v}" onchange="update({i})" {"checked" if r.get("recovery_type")==v else ""}><span style="color:#cbd5e1;margin-left:4px;font-size:13px;">{v}</span></label>'
        for v in ["user_redirect","user_correction","agent_self_recovery","session_end","false_positive"]
      )}
    </div>

    <div style="margin-bottom:12px;">
      <div style="color:#94a3b8;font-size:12px;margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em;">Failure description <span style="text-transform:none;color:#475569;">(1 sentence)</span></div>
      <textarea id="desc-{i}" rows="2" onchange="update({i})" oninput="update({i})"
        style="width:100%;box-sizing:border-box;background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:5px;padding:8px;font-size:13px;font-family:inherit;resize:vertical;"
        placeholder="What went wrong?">{_e(r.get('failure_description',''))}</textarea>
    </div>

    <div>
      <div style="color:#94a3b8;font-size:12px;margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em;">Notes</div>
      <textarea id="notes-{i}" rows="1" onchange="update({i})" oninput="update({i})"
        style="width:100%;box-sizing:border-box;background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:5px;padding:8px;font-size:13px;font-family:inherit;resize:vertical;"
        placeholder="Anything else">{_e(r.get('notes',''))}</textarea>
    </div>
  </div>
</div>""")


    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Failure Labeler</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#080c14; color:#e2e8f0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }}
  #sidebar {{ position:fixed;top:0;right:0;width:220px;height:100vh;background:#0d1117;border-left:1px solid #1e293b;overflow-y:auto;padding:14px; }}
  #main {{ margin-right:230px; padding:28px 32px; max-width:860px; }}
  .nav-item {{ display:block;padding:5px 8px;border-radius:5px;font-size:12px;cursor:pointer;margin-bottom:3px;color:#64748b;text-decoration:none; }}
  .nav-item:hover {{ background:#1e293b; }}
  .nav-item.done {{ color:#86efac; }}
  .nav-item.active {{ background:#1e293b;color:#e2e8f0; }}
  textarea:focus {{ outline:1px solid #3b82f6; }}
</style>
</head>
<body>

<div id="sidebar">
  <div style="font-size:12px;color:#94a3b8;margin-bottom:4px;text-transform:uppercase;letter-spacing:.05em;">Cases</div>
  <div id="nav-counter" style="font-size:12px;color:#64748b;margin-bottom:10px;"></div>
  {''.join(f'<a class="nav-item" id="nav-{i}" href="#card-{i}" onclick="setActive({i})">{i+1}. {_e(r["inflection_type"])}</a>' for i, r in enumerate(records))}
  <div style="margin-top:16px;">
    <button onclick="saveJSON()" style="width:100%;padding:8px;background:#1e40af;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:13px;">⬇ Save labels.json</button>
  </div>
  <div id="save-status" style="margin-top:6px;font-size:11px;color:#64748b;text-align:center;"></div>
</div>

<div id="main">
  <h2 style="color:#e2e8f0;margin-bottom:4px;">Failure Labeler</h2>
  <p style="color:#64748b;font-size:13px;margin-bottom:24px;">{len(records)} candidates — fill in each card, then click Save.</p>
  {''.join(cards)}
</div>

<script>
const records = {json.dumps(records)};
const labels = records.map(r => ({{
  inflection_id: r.inflection_id,
  genuine: r.genuine,
  failure_description: r.failure_description || "",
  recovery_type: r.recovery_type || "",
  notes: r.notes || "",
}}));

function update(i) {{
  const g = document.querySelector(`input[name="genuine-${{i}}"]:checked`);
  const rc = document.querySelector(`input[name="recovery-${{i}}"]:checked`);
  labels[i].genuine = g ? (g.value === "true") : null;
  labels[i].recovery_type = rc ? rc.value : "";
  labels[i].failure_description = document.getElementById(`desc-${{i}}`).value;
  labels[i].notes = document.getElementById(`notes-${{i}}`).value;

  const done = labels[i].genuine !== null && labels[i].recovery_type.length > 0;
  document.getElementById(`status-${{i}}`).textContent = done ? "✓ labeled" : "unlabeled";
  document.getElementById(`status-${{i}}`).style.color = done ? "#86efac" : "#64748b";
  const nav = document.getElementById(`nav-${{i}}`);
  if (done) nav.classList.add("done"); else nav.classList.remove("done");
  updateCounter();
}}

function updateCounter() {{
  const done = labels.filter(l => l.genuine !== null && l.failure_description.trim()).length;
  document.getElementById("nav-counter").textContent = done + " / " + labels.length + " labeled";
}}

function setActive(i) {{
  document.querySelectorAll(".nav-item").forEach(el => el.classList.remove("active"));
  document.getElementById(`nav-${{i}}`).classList.add("active");
}}

function saveJSON() {{
  // Merge labels back into full records
  const out = records.map((r, i) => Object.assign({{...r}}, labels[i]));
  const blob = new Blob([JSON.stringify(out, null, 2)], {{type: "application/json"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "labeled_failures.json";
  a.click();
  document.getElementById("save-status").textContent = "Downloaded!";
  setTimeout(() => document.getElementById("save-status").textContent = "", 2000);
}}

// Init
labels.forEach((_, i) => update(i));
updateCounter();
</script>
</body>
</html>"""
