import { useCurrentFrame, interpolate } from "remotion";
import { C } from "../colors";

const INSIGHTS = [
  {
    color: C.blue,
    icon: "🎯",
    title: "Granularity beat method",
    body: "Per-exchange scoring & boundary detection produced low precision and high noise on real coding-agent traces, and were hard to verify. Per-chunk extraction won.",
  },
  {
    color: C.purple,
    icon: "🔬",
    title: "The taxonomy needed audits too",
    body: "A “timeless fact” memory type turned out to be unverifiable from a single chunk — cut after audits showed it had the worst error rate of any type.",
  },
  {
    color: C.green,
    icon: "⚖️",
    title: "Model choice, decided empirically",
    body: "gemma3 vs. gemma4 vs. olmo3-think vs. Gemini 2.5 Flash — settled by real head-to-head runs on real traces, not assumptions.",
  },
];

const Card = ({ insight, visible }) => (
  <div style={{
    opacity: visible ? 1 : 0,
    transform: `translateY(${visible ? 0 : 16}px)`,
    transition: "all 0.3s",
    background: C.surface,
    border: `1px solid ${C.border}`,
    borderRadius: 12,
    padding: "28px 30px",
    width: 510,
    borderTop: `3px solid ${insight.color}`,
  }}>
    <div style={{ fontSize: 37, marginBottom: 10 }}>{insight.icon}</div>
    <div style={{ fontFamily: "system-ui", fontSize: 27, fontWeight: 700, color: insight.color, marginBottom: 10 }}>
      {insight.title}
    </div>
    <div style={{ fontFamily: "system-ui", fontSize: 20, color: C.dim, lineHeight: 1.55 }}>
      {insight.body}
    </div>
  </div>
);

export const ResearchScene = () => {
  const frame = useCurrentFrame();
  const titleOpacity = interpolate(frame, [0, 15], [0, 1], { extrapolateRight: "clamp" });
  const subOpacity = interpolate(frame, [10, 30], [0, 1], { extrapolateRight: "clamp" });

  const cardStart = 40;
  const cardStagger = 45;

  return (
    <div style={{
      width: "100%", height: "100%",
      background: C.bg,
      display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center",
      gap: 28,
      padding: "0 60px",
    }}>
      <div style={{ opacity: titleOpacity, fontFamily: "system-ui", fontSize: 48, fontWeight: 700, color: C.white, textAlign: "center" }}>
        What Debugging the Pipeline Taught Us
      </div>
      <div style={{ opacity: subOpacity, fontFamily: "system-ui", fontSize: 23, color: C.dim, textAlign: "center", maxWidth: 920 }}>
        Beyond the personal use case — practical patterns for debugging autonomous coding agents generally
      </div>

      <div style={{ display: "flex", gap: 24 }}>
        {INSIGHTS.map((insight, i) => (
          <Card key={insight.title} insight={insight} visible={frame > cardStart + i * cardStagger} />
        ))}
      </div>
    </div>
  );
};
