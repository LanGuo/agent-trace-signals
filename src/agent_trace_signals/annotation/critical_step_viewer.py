"""HTML labeling viewer for critical_step candidates (Stage 4).

Self-contained single-file HTML with inline CSS / JS. No server. The user
labels each card, clicks "Download labels JSON", and replaces the local
labeled_critical_steps.json file.
"""

from __future__ import annotations

import json


_TAG_COLORS = {
    "failure_critical":  "#ef4444",
    "success_critical":  "#10b981",
    "recovery_critical": "#8b5cf6",
}
_DEFAULT_COLOR = "#94a3b8"

_FAILURE_MODES = [
    "wrong_target",
    "tool_misread",
    "evidence_thin",
    "premature_done",
    "trajectory_inflation",
    "other",
]
_TAG_OPTIONS = [
    "failure_critical",
    "success_critical",
    "recovery_critical",
    "none-of-these",
]


def _e(s) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _fmt_num(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.3f}"
    except (TypeError, ValueError):
        return str(v)


def _render_surrounding(surrounding: list, card_idx: int) -> str:
    """Render ±3 surrounding exchanges as a compact vertical timeline.

    Non-focus rows include a "⚑ missed?" toggle so the reviewer can flag
    exchanges the detector silently skipped (FN candidates).
    """
    if not surrounding:
        return ""
    rows = []
    for s in surrounding:
        offset = s.get("offset", 0)
        is_focus = s.get("is_focus", False)
        ex_i = s.get("exchange_idx", "?")
        ex_type = s.get("exchange_type", "?")
        user_brief = _e(s.get("user_brief", "") or "")
        agent_brief = _e(s.get("agent_brief", "") or "")
        score = s.get("score")
        critical = s.get("critical_tag")
        tool_count = s.get("tool_count", 0)
        tool_errs = s.get("tool_error_count", 0)

        # Offset label
        if offset == 0:
            offset_label = "← THIS"
            row_bg = "#1e3a5f"
            border = "border-left:3px solid #60a5fa;"
        elif offset < 0:
            offset_label = f"{offset}"
            row_bg = "#0d1117"
            border = ""
        else:
            offset_label = f"+{offset}"
            row_bg = "#0d1117"
            border = ""

        score_html = ""
        if score is not None:
            color = "#86efac" if score >= 0.20 else "#fca5a5" if score <= 0.10 else "#cbd5e1"
            score_html = f'<span style="color:{color};font-size:11px;">s={score:.2f}</span>'

        crit_badge = ""
        if critical:
            crit_color = {
                "failure_critical": "#fca5a5",
                "success_critical": "#86efac",
                "recovery_critical": "#fcd34d",
            }.get(critical, "#cbd5e1")
            crit_badge = f'<span style="color:{crit_color};font-size:10px;margin-left:6px;">🔴 {critical[:3]}</span>'

        tool_html = ""
        if tool_count:
            err_str = f"/{tool_errs}❌" if tool_errs else ""
            tool_html = f'<span style="color:#94a3b8;font-size:11px;margin-left:8px;">🔧{tool_count}{err_str}</span>'

        user_html = (f'<span style="color:#fde68a;">U:</span> <span style="color:#cbd5e1;">{user_brief}</span><br>'
                     if user_brief else "")
        agent_html = (f'<span style="color:#a5b4fc;">A:</span> <span style="color:#cbd5e1;">{agent_brief}</span>'
                      if agent_brief else "<span style='color:#64748b;'>(no agent action)</span>")

        # FN flag button — only on non-focus rows; focus row is the candidate itself
        fn_btn = ""
        if not is_focus:
            fn_btn = (
                f'<button id="fn-btn-{card_idx}-{ex_i}" '
                f'onclick="toggleFN({card_idx}, {ex_i}, this)" '
                f'title="Flag this exchange as a missed critical step (FN)" '
                f'style="padding:2px 8px;border:1px solid #334155;border-radius:4px;cursor:pointer;'
                f'font-size:10px;background:#1e293b;color:#94a3b8;margin-left:8px;">⚑ missed?</button>'
            )

        rows.append(f'''
        <div style="padding:8px 12px;background:{row_bg};border-radius:5px;{border}margin-bottom:6px;font-size:12px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
            <div>
              <span style="color:#64748b;font-family:monospace;font-size:11px;width:42px;display:inline-block;">{offset_label}</span>
              <span style="color:#64748b;font-size:11px;">ex {ex_i}</span>
              <span style="color:#64748b;font-size:11px;margin-left:8px;">[{ex_type}]</span>
              {tool_html}
              {fn_btn}
            </div>
            <div>{score_html}{crit_badge}</div>
          </div>
          <div style="margin-left:42px;line-height:1.4;">
            {user_html}{agent_html}
          </div>
        </div>
        ''')
    return f'''
    <div style="margin:10px 0;padding:10px 12px;background:#1a1f2e;border-radius:6px;border-left:3px solid #cbd5e1;">
      <div style="color:#cbd5e1;font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;">
        surrounding context (±3 turns) — click <span style="color:#f59e0b;">⚑ missed?</span> on any neighbor you think should have been flagged
      </div>
      {"".join(rows)}
    </div>
    '''


def _card(i: int, r: dict) -> str:
    tag = r.get("tag", "")
    color = _TAG_COLORS.get(tag, _DEFAULT_COLOR)
    fm = r.get("failure_mode") or ""
    ctx = r.get("context") or {}
    label = r.get("label") or {}
    sid = r.get("session_id", "")
    sid_short = sid[:16] + ("…" if len(sid) > 16 else "")

    fm_badge = (
        f'<span style="margin-left:8px;font-size:11px;padding:2px 8px;'
        f'border-radius:3px;background:#3b2f00;color:#fde68a;">{_e(fm)}</span>'
        if fm else ""
    )

    pv = r.get("progress_vector") or {}
    cv = r.get("cost_vector") or {}
    metric_bits = []
    for k, v in list(pv.items())[:6]:
        metric_bits.append(
            f'<span style="color:#94a3b8;">{_e(k)}:</span> '
            f'<span style="color:#e2e8f0;">{_fmt_num(v)}</span>'
        )
    for k, v in list(cv.items())[:4]:
        metric_bits.append(
            f'<span style="color:#94a3b8;">{_e("cost." + k)}:</span> '
            f'<span style="color:#e2e8f0;">{_fmt_num(v)}</span>'
        )
    metrics_html = " &nbsp;·&nbsp; ".join(metric_bits)

    summary = _e(r.get("agent_action_summary", "") or "(no agent_action_summary)")
    # Surrounding ±3 turns (compact one-liners) so reviewer has context.
    surrounding = ctx.get("surrounding") or []
    surrounding_html = _render_surrounding(surrounding, i)
    # New rich context fields
    substantial = _e(ctx.get("substantial_directive", "") or "(no substantial directive found)")
    sd_at = ctx.get("substantial_directive_at", -1)
    immediate = _e(ctx.get("immediate_user_text", "") or "")
    this_ex = ctx.get("this_exchange", {}) or {}
    raw_serial = _e(this_ex.get("raw_serialized", "") or "(no exchange detail available)")
    ex_type = _e(this_ex.get("exchange_type", "") or "?")
    # Backward compat: old labels JSON may still have evidence_text_preview
    legacy_evidence = _e(ctx.get("evidence_text_preview", "") or "") if "evidence_text_preview" in ctx else ""

    tag_select_options = "".join(
        f'<option value="{_e(t)}" {"selected" if label.get("should_have_been_tag") == t else ""}>{_e(t)}</option>'
        for t in _TAG_OPTIONS
    )
    fm_select_options = "".join(
        f'<option value="{_e(m)}" {"selected" if label.get("should_have_been_failure_mode") == m else ""}>{_e(m)}</option>'
        for m in _FAILURE_MODES
    )

    is_tp = label.get("is_true_positive")
    btn_yes_style = "background:#14532d;color:#86efac;" if is_tp is True else "background:#1e293b;color:#94a3b8;"
    btn_no_style = "background:#4c1d1d;color:#fca5a5;" if is_tp is False else "background:#1e293b;color:#94a3b8;"
    btn_un_style = "background:#1e293b;color:#94a3b8;" if is_tp is None else "background:#0d1117;color:#475569;"

    return f"""
<div id="card-{i}" data-idx="{i}" data-tag="{_e(tag)}" data-failure-mode="{_e(fm)}"
     style="margin:0 0 28px 0;padding:20px;background:#0f1117;border:1px solid #1e293b;border-radius:10px;">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;">
    <div>
      <span style="font-size:15px;font-weight:600;color:{color};">{_e(tag)}</span>
      {fm_badge}
      <span style="margin-left:10px;color:#64748b;font-size:12px;">session: {_e(sid_short)} · exchange {r.get('exchange_idx')}</span>
    </div>
    <div id="status-{i}" style="font-size:13px;color:#64748b;">unlabeled</div>
  </div>

  <div style="margin-bottom:8px;font-size:12px;">
    <span style="color:#94a3b8;">score:</span> <span style="color:#e2e8f0;">{_fmt_num(r.get('score'))}</span>
    &nbsp;·&nbsp;
    <span style="color:#94a3b8;">delta:</span> <span style="color:#e2e8f0;">{_fmt_num(r.get('delta'))}</span>
    &nbsp;·&nbsp;
    <span style="color:#94a3b8;">prev:</span> <span style="color:#e2e8f0;">{_fmt_num(ctx.get('preceding_score'))}</span>
    &nbsp;·&nbsp;
    <span style="color:#94a3b8;">next:</span> <span style="color:#e2e8f0;">{_fmt_num(ctx.get('following_score'))}</span>
  </div>
  <div style="margin-bottom:12px;font-size:12px;">{metrics_html}</div>

  <div style="margin:10px 0;padding:10px 12px;background:#1a1f2e;border-radius:6px;border-left:3px solid #fbbf24;">
    <div style="color:#fbbf24;font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">substantial user directive {("(from exchange " + str(sd_at) + ")") if sd_at is not None and sd_at >= 0 else ""}</div>
    <div style="color:#fef3c7;font-size:13px;white-space:pre-wrap;">{substantial}</div>
  </div>

  {f'''<div style="margin:10px 0;padding:8px 12px;background:#161b22;border-radius:6px;font-size:12px;color:#94a3b8;">
    <strong style="color:#cbd5e1;">immediately preceding directive:</strong> <span style="color:#e2e8f0;">{immediate}</span>
  </div>''' if immediate and immediate != substantial else ""}

  {surrounding_html}

  <div style="margin:10px 0;padding:10px 12px;background:#1a1f2e;border-radius:6px;border-left:3px solid #60a5fa;">
    <div style="color:#60a5fa;font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">this exchange ({ex_type}) — full detail</div>
    <pre style="max-height:360px;overflow:auto;background:#020617;color:#cbd5e1;font-size:11px;padding:10px;border:1px solid #1e293b;border-radius:6px;white-space:pre-wrap;word-break:break-word;margin:0;">{raw_serial}</pre>
  </div>

  <div style="margin:10px 0;padding:10px 12px;background:#161b22;border-radius:6px;">
    <div style="color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">observer's one-line summary</div>
    <div style="color:#cbd5e1;font-size:12px;font-style:italic;">{summary}</div>
  </div>

  {f'''<div style="margin:10px 0;">
    <details><summary style="color:#64748b;font-size:11px;cursor:pointer;">legacy evidence_text_preview (chunk-level, may overlap with adjacent steps)</summary>
    <pre style="max-height:200px;overflow:auto;background:#020617;color:#475569;font-size:10px;padding:8px;border:1px solid #1e293b;border-radius:6px;white-space:pre-wrap;word-break:break-word;margin-top:6px;">{legacy_evidence}</pre>
    </details>
  </div>''' if legacy_evidence else ""}

  <!-- Label controls -->
  <div style="padding:14px;background:#0d1117;border:1px solid #1e293b;border-radius:8px;margin-top:14px;">

    <div style="margin-bottom:12px;">
      <div style="color:#94a3b8;font-size:12px;margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em;">Is this a true positive?</div>
      <button id="tp-yes-{i}" onclick="setTP({i}, true)" style="padding:6px 14px;border:none;border-radius:5px;cursor:pointer;margin-right:8px;font-size:13px;{btn_yes_style}">✓ Yes</button>
      <button id="tp-no-{i}" onclick="setTP({i}, false)" style="padding:6px 14px;border:none;border-radius:5px;cursor:pointer;margin-right:8px;font-size:13px;{btn_no_style}">✗ No (FP)</button>
      <button id="tp-un-{i}" onclick="setTP({i}, null)" style="padding:6px 14px;border:none;border-radius:5px;cursor:pointer;font-size:13px;{btn_un_style}">unset</button>
    </div>

    <div style="margin-bottom:12px;">
      <div style="color:#94a3b8;font-size:12px;margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em;">If wrong, what tag should it be? (optional)</div>
      <select id="should-tag-{i}" onchange="update({i})" style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:5px;padding:6px;font-size:13px;">
        <option value="">(unset)</option>
        {tag_select_options}
      </select>
    </div>

    <div style="margin-bottom:12px;">
      <div style="color:#94a3b8;font-size:12px;margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em;">If wrong, what failure_mode? (optional)</div>
      <select id="should-mode-{i}" onchange="update({i})" style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:5px;padding:6px;font-size:13px;">
        <option value="">(unset)</option>
        {fm_select_options}
      </select>
    </div>

    <div>
      <div style="color:#94a3b8;font-size:12px;margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em;">Notes</div>
      <textarea id="notes-{i}" rows="2" oninput="update({i})"
        style="width:100%;box-sizing:border-box;background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:5px;padding:8px;font-size:13px;font-family:inherit;resize:vertical;"
        placeholder="Why true / false positive?">{_e(label.get('notes', '') or '')}</textarea>
    </div>
  </div>
</div>"""


def generate(payload: dict) -> str:
    """Render a self-contained HTML page from a labels JSON payload dict.

    payload follows the schema produced by critical_step_labeler.generate_labels.
    """
    records = payload.get("records", [])
    thresholds = payload.get("thresholds", {})

    cards_html = "".join(_card(i, r) for i, r in enumerate(records))

    tag_counts: dict[str, int] = {}
    for r in records:
        tag_counts[r.get("tag", "?")] = tag_counts.get(r.get("tag", "?"), 0) + 1
    tag_breakdown_html = " &nbsp;·&nbsp; ".join(
        f'<span style="color:{_TAG_COLORS.get(t, _DEFAULT_COLOR)};">{_e(t)}: {c}</span>'
        for t, c in sorted(tag_counts.items())
    ) or "—"

    nav_items = "".join(
        f'<a class="nav-item" id="nav-{i}" href="#card-{i}" onclick="setActive({i})">'
        f'{i+1}. <span style="color:{_TAG_COLORS.get(r.get("tag", ""), _DEFAULT_COLOR)};">{_e(r.get("tag", ""))}</span></a>'
        for i, r in enumerate(records)
    )

    # When embedding JSON inside a <script> block, any literal "</script>"
    # in tool outputs (e.g. agent-scaffolded index.html with Vite tags)
    # would prematurely close the script tag and break all JS handlers
    # (setTP, update, saveJSON). Escape "</" to "<\/" — this is identical
    # to the browser for string equality but invisible to the HTML parser.
    # Same defensive escape for </style> and other terminators just in case.
    payload_json = json.dumps(payload).replace("</", "<\\/")
    candidate_count = len(records)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Critical-Step Labeler</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#080c14; color:#e2e8f0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }}
  #sidebar {{ position:fixed;top:0;right:0;width:260px;height:100vh;background:#0d1117;border-left:1px solid #1e293b;overflow-y:auto;padding:14px; }}
  #main {{ margin-right:270px; padding:28px 32px; max-width:980px; }}
  .nav-item {{ display:block;padding:5px 8px;border-radius:5px;font-size:12px;cursor:pointer;margin-bottom:3px;color:#94a3b8;text-decoration:none; }}
  .nav-item:hover {{ background:#1e293b; }}
  .nav-item.tp-true {{ border-left:3px solid #10b981; }}
  .nav-item.tp-false {{ border-left:3px solid #ef4444; }}
  .nav-item.active {{ background:#1e293b;color:#e2e8f0; }}
  textarea:focus, select:focus {{ outline:1px solid #3b82f6; }}
  .prec-box {{ padding:8px 10px;background:#0d1117;border:1px solid #1e293b;border-radius:6px;margin-bottom:6px;font-size:12px; }}
</style>
</head>
<body>

<div id="sidebar">
  <div style="font-size:13px;font-weight:600;color:#e2e8f0;margin-bottom:4px;">Critical-Step Labeler</div>
  <div id="summary-stats" style="font-size:11px;color:#94a3b8;margin-bottom:12px;"></div>

  <div class="prec-box">
    <div style="color:#94a3b8;text-transform:uppercase;font-size:10px;letter-spacing:.05em;margin-bottom:4px;">Precision (live)</div>
    <div id="prec-overall" style="color:#e2e8f0;font-size:14px;font-weight:600;">—</div>
    <div id="prec-bytag" style="margin-top:6px;font-size:11px;color:#cbd5e1;"></div>
  </div>
  <div class="prec-box">
    <div style="color:#94a3b8;text-transform:uppercase;font-size:10px;letter-spacing:.05em;margin-bottom:4px;">Recall lb (⚑ flagged FN)</div>
    <div id="recall-estimate" style="color:#fde68a;font-size:12px;">— (flag missed events in timeline)</div>
  </div>

  <div style="margin:14px 0 6px 0;">
    <button onclick="saveJSON()" style="width:100%;padding:8px;background:#1e40af;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:13px;">⬇ Download labels JSON</button>
  </div>
  <div id="save-status" style="margin-top:6px;font-size:11px;color:#64748b;text-align:center;"></div>

  <div style="font-size:11px;color:#94a3b8;margin:14px 0 4px 0;text-transform:uppercase;letter-spacing:.05em;">Candidates</div>
  {nav_items}
</div>

<div id="main">
  <h2 style="color:#e2e8f0;margin-bottom:4px;">Critical-Step Labeler</h2>
  <p style="color:#64748b;font-size:13px;margin-bottom:8px;">
    <span id="candidate-count">{candidate_count}</span> candidate critical_steps —
    label each one true / false / unset, then download JSON.
  </p>
  <p style="color:#64748b;font-size:12px;margin-bottom:24px;">
    Thresholds: <code style="color:#cbd5e1;">{_e(json.dumps(thresholds))}</code>
    &nbsp;·&nbsp; By tag: {tag_breakdown_html}
  </p>
  {cards_html}
</div>

<script>
const PAYLOAD = {payload_json};
const records = PAYLOAD.records;
const labels = records.map(r => Object.assign({{
  is_true_positive: null,
  should_have_been_tag: null,
  should_have_been_failure_mode: null,
  notes: "",
  fn_flags: []
}}, r.label || {{}}));

// fnFlags[i] = Set of exchange_idx values flagged as missed critical steps
const fnFlags = labels.map(l => new Set((l.fn_flags || []).map(f => f.exchange_idx)));

function toggleFN(cardIdx, exchangeIdx, btn) {{
  const s = fnFlags[cardIdx];
  if (s.has(exchangeIdx)) {{
    s.delete(exchangeIdx);
    btn.style.background = "#1e293b";
    btn.style.color = "#94a3b8";
    btn.textContent = "⚑ missed?";
  }} else {{
    s.add(exchangeIdx);
    btn.style.background = "#78350f";
    btn.style.color = "#fde68a";
    btn.textContent = "⚑ flagged FN";
  }}
  // Sync back to labels
  const surr = (records[cardIdx].context || {{}}).surrounding || [];
  labels[cardIdx].fn_flags = Array.from(s).map(eidx => {{
    const row = surr.find(r => r.exchange_idx === eidx) || {{}};
    return {{
      exchange_idx: eidx,
      offset: row.offset,
      exchange_type: row.exchange_type,
      user_brief: row.user_brief,
      agent_brief: row.agent_brief,
      score: row.score,
    }};
  }});
  recomputePrecision();
}}

function setTP(i, v) {{
  labels[i].is_true_positive = v;
  refreshTPButtons(i);
  update(i);
}}

function refreshTPButtons(i) {{
  const v = labels[i].is_true_positive;
  const yes = document.getElementById(`tp-yes-${{i}}`);
  const no  = document.getElementById(`tp-no-${{i}}`);
  const un  = document.getElementById(`tp-un-${{i}}`);
  yes.style.cssText = "padding:6px 14px;border:none;border-radius:5px;cursor:pointer;margin-right:8px;font-size:13px;" +
    (v === true ? "background:#14532d;color:#86efac;" : "background:#1e293b;color:#94a3b8;");
  no.style.cssText = "padding:6px 14px;border:none;border-radius:5px;cursor:pointer;margin-right:8px;font-size:13px;" +
    (v === false ? "background:#4c1d1d;color:#fca5a5;" : "background:#1e293b;color:#94a3b8;");
  un.style.cssText = "padding:6px 14px;border:none;border-radius:5px;cursor:pointer;font-size:13px;" +
    (v === null ? "background:#1e293b;color:#94a3b8;" : "background:#0d1117;color:#475569;");
}}

function update(i) {{
  const stEl = document.getElementById(`should-tag-${{i}}`);
  const smEl = document.getElementById(`should-mode-${{i}}`);
  const ntEl = document.getElementById(`notes-${{i}}`);
  labels[i].should_have_been_tag = stEl.value || null;
  labels[i].should_have_been_failure_mode = smEl.value || null;
  labels[i].notes = ntEl.value;

  const v = labels[i].is_true_positive;
  const status = document.getElementById(`status-${{i}}`);
  if (v === true) {{ status.textContent = "✓ true positive"; status.style.color = "#86efac"; }}
  else if (v === false) {{ status.textContent = "✗ false positive"; status.style.color = "#fca5a5"; }}
  else {{ status.textContent = "unlabeled"; status.style.color = "#64748b"; }}

  const nav = document.getElementById(`nav-${{i}}`);
  nav.classList.remove("tp-true", "tp-false");
  if (v === true) nav.classList.add("tp-true");
  else if (v === false) nav.classList.add("tp-false");

  recomputePrecision();
}}

function recomputePrecision() {{
  let tp = 0, fp = 0, fn_count = 0;
  const byTag = {{}};
  records.forEach((r, i) => {{
    const v = labels[i].is_true_positive;
    if (v !== null && v !== undefined) {{
      const t = r.tag || "?";
      if (!byTag[t]) byTag[t] = [0, 0];
      if (v === true) {{ tp++; byTag[t][0]++; }}
      else {{ fp++; byTag[t][1]++; }}
    }}
    fn_count += (labels[i].fn_flags || []).length;
  }});
  const labeled = tp + fp;
  const pct = records.length > 0 ? (100 * labeled / records.length).toFixed(0) : "0";
  document.getElementById("summary-stats").textContent =
    `${{records.length}} total · ${{labeled}} labeled (${{pct}}%) · ${{fn_count}} FN flagged`;

  const overall = labeled > 0 ? (tp / labeled) : null;
  const gatePass = overall !== null && overall >= 0.8 && labeled >= 20;
  const overallEl = document.getElementById("prec-overall");
  overallEl.textContent = overall === null
    ? "— (label some)"
    : `${{(overall * 100).toFixed(1)}}% (${{tp}}/${{labeled}})${{gatePass ? " ✓ ship gate" : ""}}`;
  overallEl.style.color = gatePass ? "#86efac" : "#e2e8f0";

  // Recall estimate: TP / (TP + FN_flagged) — lower bound (only ±3 window visible)
  const recallEl = document.getElementById("recall-estimate");
  if (recallEl) {{
    if (tp + fn_count > 0) {{
      const r = tp / (tp + fn_count);
      recallEl.textContent = `recall lb: ${{(r * 100).toFixed(1)}}% (${{tp}}/${{tp + fn_count}}) · ${{fn_count}} FN`;
    }} else {{
      recallEl.textContent = fn_count > 0 ? `${{fn_count}} FN flagged` : "—";
    }}
  }}

  const bytagHtml = Object.keys(byTag).sort().map(t => {{
    const [a, b] = byTag[t];
    const p = (a + b) > 0 ? (100 * a / (a + b)).toFixed(0) + "%" : "—";
    return `<div>${{t}}: ${{p}} <span style="color:#64748b;">(${{a}}/${{a+b}})</span></div>`;
  }}).join("");
  document.getElementById("prec-bytag").innerHTML = bytagHtml;
}}

function setActive(i) {{
  document.querySelectorAll(".nav-item").forEach(el => el.classList.remove("active"));
  document.getElementById(`nav-${{i}}`).classList.add("active");
}}

function saveJSON() {{
  const out = {{
    version: PAYLOAD.version || 1,
    generated_at: PAYLOAD.generated_at,
    thresholds: PAYLOAD.thresholds,
    records: records.map((r, i) => Object.assign({{}}, r, {{label: labels[i]}})),
  }};
  const blob = new Blob([JSON.stringify(out, null, 2)], {{type: "application/json"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "labeled_critical_steps.json";
  a.click();
  document.getElementById("save-status").textContent = "Downloaded!";
  setTimeout(() => document.getElementById("save-status").textContent = "", 2000);
}}

// Restore any pre-loaded FN flags (from existing labels JSON)
records.forEach((_, i) => {{
  (labels[i].fn_flags || []).forEach(f => {{
    const btn = document.getElementById(`fn-btn-${{i}}-${{f.exchange_idx}}`);
    if (btn) {{
      btn.style.background = "#78350f";
      btn.style.color = "#fde68a";
      btn.textContent = "⚑ flagged FN";
    }}
  }});
}});

// Init
labels.forEach((_, i) => refreshTPButtons(i));
labels.forEach((_, i) => update(i));
</script>
</body>
</html>"""
