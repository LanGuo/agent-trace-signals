import { useCurrentFrame, interpolate } from "remotion";
import { C } from "../colors";

// Real numbers from experiments/exp10_retrieval_h2h/h2h_results.json — 14 facts from
// this repo's own design/design_decisions.md, each asked a precise and a fuzzy
// (paraphrased) way, 28 cases total. Static point-in-time study, not live-recomputed.
const METHODS = [
  {
    color: C.red,
    name: "Raw session grep",
    hit: "28/28",
    hitNote: "≈ meaningless here",
    tokens: "106,309",
    tokensNote: "tokens/query avg (max 824,918)",
    body: "The keyword exists somewhere in 118MB of transcript — not that it found the right answer.",
  },
  {
    color: C.yellow,
    name: "Design log grep",
    hit: "10/28",
    hitNote: "hit",
    tokens: "69",
    tokensNote: "tokens/query avg",
    body: "Cheap and fast, but scope-limited (only what got manually written up) and brittle to exact phrasing.",
  },
  {
    color: C.green,
    name: "ats (recall + chunk_search, MCP)",
    hit: "20/28",
    hitNote: "hit",
    tokens: "3,121",
    tokensNote: "tokens/query avg",
    body: "The full MCP surface Claude Code already has — recall + chunk_search, workspace-scoped.",
    highlight: true,
  },
];

const Card = ({ m, visible }) => (
  <div style={{
    opacity: visible ? 1 : 0,
    transform: `translateY(${visible ? 0 : 16}px)`,
    transition: "all 0.3s",
    background: C.surface,
    border: `1px solid ${m.highlight ? m.color : C.border}`,
    boxShadow: m.highlight ? `0 0 28px ${m.color}33` : "none",
    borderRadius: 12,
    padding: "26px 28px",
    width: 500,
    borderTop: `3px solid ${m.color}`,
  }}>
    <div style={{ fontFamily: "system-ui", fontSize: 21, fontWeight: 700, color: C.white, marginBottom: 16, minHeight: 52 }}>
      {m.name}
    </div>
    <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 4 }}>
      <span style={{ fontFamily: "monospace", fontSize: 42, fontWeight: 700, color: m.color }}>{m.hit}</span>
      <span style={{ fontFamily: "system-ui", fontSize: 16.5, color: C.dim }}>{m.hitNote}</span>
    </div>
    <div style={{ fontFamily: "system-ui", fontSize: 17.5, color: C.white, marginBottom: 16 }}>
      <span style={{ fontFamily: "monospace", fontWeight: 700 }}>{m.tokens}</span> {m.tokensNote}
    </div>
    <div style={{ fontFamily: "system-ui", fontSize: 17, color: C.dim, lineHeight: 1.55 }}>
      {m.body}
    </div>
  </div>
);

export const EvaluationScene = () => {
  const frame = useCurrentFrame();
  const titleOpacity = interpolate(frame, [0, 15], [0, 1], { extrapolateRight: "clamp" });
  const subOpacity = interpolate(frame, [10, 30], [0, 1], { extrapolateRight: "clamp" });

  const cardStart = 40;
  const cardStagger = 40;

  return (
    <div style={{
      width: "100%", height: "100%",
      background: C.bg,
      display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center",
      gap: 24,
      padding: "0 60px",
    }}>
      <div style={{ opacity: titleOpacity, fontFamily: "system-ui", fontSize: 48, fontWeight: 700, color: C.white, textAlign: "center" }}>
        Matches Grep's Practical Hit Rate — at ~1/30th the Cost
      </div>
      <div style={{ opacity: subOpacity, fontFamily: "system-ui", fontSize: 22, color: C.dim, textAlign: "center", maxWidth: 1100 }}>
        14 facts from this repo's own design log, each asked precisely and paraphrased — 28 cases total. Hit rate alone doesn't tell you the cost.
      </div>

      <div style={{ display: "flex", gap: 24, marginTop: 8 }}>
        {METHODS.map((m, i) => (
          <Card key={m.name} m={m} visible={frame > cardStart + i * cardStagger} />
        ))}
      </div>
    </div>
  );
};
