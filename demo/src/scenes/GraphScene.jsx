import { useCurrentFrame, interpolate } from "remotion";
import { C } from "../colors";
import { TerminalWindow, TypewriterLine, Prompt } from "../Terminal";

// Real neighborhood from `ats graph-walk eff450c3` — the three 2026-07-15 sessions
// auditing gemma3/gemma4 entity+memory extraction, plus a June session they share
// a "reliable agent observability" cluster memory with.
const NODES = [
  { id: "eff450c3", x: 150, y: 50,  labelPos: "above" },
  { id: "0b3b0725", x: 380, y: 30,  labelPos: "above" },
  { id: "dbc42e0e", x: 380, y: 170, labelPos: "below" },
  { id: "f994bf68", x: 570, y: 100, labelPos: "below" },
];

const EDGES = [
  { from: "eff450c3", to: "0b3b0725", type: "workspace" },
  { from: "eff450c3", to: "dbc42e0e", type: "structural" },
  { from: "0b3b0725", to: "dbc42e0e", type: "structural" },
  { from: "0b3b0725", to: "f994bf68", type: "shared_memory" },
  { from: "dbc42e0e", to: "f994bf68", type: "shared_memory" },
];

const EDGE_TYPES = [
  { key: "workspace",            label: "Same workspace",              n: "1,626", color: C.blue },
  { key: "structural",           label: "Shared file / commit / PR",   n: "2,610", color: C.green },
  { key: "shared_memory",        label: "Shared cluster memory",       n: "5,602", color: C.purple },
  { key: "structural_similarity", label: "Structural embedding cosine", n: "824",   color: C.yellow },
];

const findNode = (id) => NODES.find((n) => n.id === id);

export const GraphScene = () => {
  const frame = useCurrentFrame();
  const titleOpacity = interpolate(frame, [0, 15], [0, 1], { extrapolateRight: "clamp" });
  const subOpacity = interpolate(frame, [10, 30], [0, 1], { extrapolateRight: "clamp" });

  const graphStart = 25;
  const legendStart = graphStart + EDGES.length * 8 + 20;
  const cmdStart = legendStart + 40;
  const cmdTextStart = cmdStart + 5;
  const cmdOutStart = cmdStart + 45;

  return (
    <div style={{
      width: "100%", height: "100%",
      background: C.bg,
      display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center",
      gap: 24,
      padding: "0 70px",
    }}>
      <div style={{ opacity: titleOpacity, fontFamily: "system-ui", fontSize: 48, fontWeight: 700, color: C.white }}>
        A Lightweight Knowledge Graph
      </div>
      <div style={{ opacity: subOpacity, fontFamily: "system-ui", fontSize: 23, color: C.dim, textAlign: "center" }}>
        Sessions linked by what they share — not by when they happened
      </div>

      <div style={{ display: "flex", gap: 56, alignItems: "center" }}>
        {/* Graph viz */}
        <svg width="640" height="250" style={{ overflow: "visible" }}>
          {EDGES.map((e, i) => {
            const a = findNode(e.from), b = findNode(e.to);
            const et = EDGE_TYPES.find((t) => t.key === e.type);
            const visible = frame > graphStart + i * 8;
            return (
              <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                stroke={et.color} strokeWidth={2}
                opacity={visible ? 0.85 : 0}
                style={{ transition: "opacity 0.3s" }}
              />
            );
          })}
          {NODES.map((n, i) => {
            const visible = frame > graphStart - 8;
            return (
              <g key={n.id} opacity={visible ? 1 : 0}>
                <circle cx={n.x} cy={n.y} r={31} fill={C.surface} stroke={C.border} strokeWidth={2} />
                <text x={n.x} y={n.y + 6} textAnchor="middle" fontFamily="monospace" fontSize="16" fill={C.white}>{n.id.slice(0, 4)}</text>
                <text x={n.x} y={n.labelPos === "above" ? n.y - 40 : n.y + 48} textAnchor="middle" fontFamily="monospace" fontSize="14" fill={C.dim}>{n.id}</text>
              </g>
            );
          })}
        </svg>

        {/* Legend */}
        {frame > legendStart && (
          <div style={{
            opacity: interpolate(frame, [legendStart, legendStart + 15], [0, 1], { extrapolateRight: "clamp" }),
            display: "flex", flexDirection: "column", gap: 12,
          }}>
            {EDGE_TYPES.map((t) => (
              <div key={t.key} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <div style={{ width: 22, height: 3, background: t.color, borderRadius: 2 }} />
                <div style={{ fontFamily: "system-ui", fontSize: 20, color: C.white }}>{t.label}</div>
                <div style={{ fontFamily: "monospace", fontSize: 19, color: C.dim }}>{t.n}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {frame > cmdStart && (
        <TerminalWindow title="ats — agent trace signals" style={{ width: 860 }}>
          <div style={{ display: "flex", alignItems: "center" }}>
            <Prompt />
            <TypewriterLine text="ats graph-walk eff450c3" startFrame={cmdTextStart} color={C.white} />
          </div>
          {frame > cmdOutStart && (
            <div style={{
              opacity: interpolate(frame, [cmdOutStart, cmdOutStart + 15], [0, 1], { extrapolateRight: "clamp" }),
              fontFamily: "monospace", fontSize: 20, color: C.dim, marginTop: 10, lineHeight: 1.6,
            }}>
              This session — one of three auditing gemma3 vs. gemma4 extraction quality — links to its two siblings via shared files (chunk_analyzer.py, entity_resolver…), to an unrelated June session via a shared cluster memory, and to dozens more via structural-embedding similarity. <span style={{ color: C.white }}>227 connections total.</span>
            </div>
          )}
        </TerminalWindow>
      )}
    </div>
  );
};
