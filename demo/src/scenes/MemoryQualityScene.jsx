import { useCurrentFrame, interpolate } from "remotion";
import { C } from "../colors";

const CARDS = [
  {
    icon: "⚡",
    color: C.purple,
    title: "Conflicts caught for free",
    body: "The same call that consolidates a cluster into one memory also checks whether any two members actually contradict each other — not just phrase it differently. Flagged pairs land in a conflicts table.",
  },
  {
    icon: "🔁",
    color: C.yellow,
    title: "Superseded, not duplicated",
    body: "When a new cluster memory's members mostly overlap with an older one's, the old one is marked superseded and points to its replacement — evolution instead of near-duplicate buildup.",
  },
  {
    icon: "📶",
    color: C.green,
    title: "Usage becomes a signal",
    body: "Every recall() and graph_walk() call now logs which memories it surfaced — a memory's real-world retrieval count, not just its mint-time evidence, becomes something you can track over time.",
  },
];

const Card = ({ card, visible }) => (
  <div style={{
    opacity: visible ? 1 : 0,
    transform: `translateY(${visible ? 0 : 16}px)`,
    transition: "all 0.3s",
    background: C.surface,
    border: `1px solid ${C.border}`,
    borderRadius: 12,
    padding: "28px 30px",
    width: 510,
    borderTop: `3px solid ${card.color}`,
  }}>
    <div style={{ fontSize: 37, marginBottom: 10 }}>{card.icon}</div>
    <div style={{ fontFamily: "system-ui", fontSize: 27, fontWeight: 700, color: card.color, marginBottom: 10 }}>
      {card.title}
    </div>
    <div style={{ fontFamily: "system-ui", fontSize: 20, color: C.dim, lineHeight: 1.55 }}>
      {card.body}
    </div>
  </div>
);

export const MemoryQualityScene = () => {
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
        Memory Quality: Checked, Merged, Measured
      </div>
      <div style={{ opacity: subOpacity, fontFamily: "system-ui", fontSize: 23, color: C.dim, textAlign: "center", maxWidth: 940 }}>
        Consolidation doesn't stop at minting — three guardrails clustering enables downstream of it
      </div>

      <div style={{ display: "flex", gap: 24 }}>
        {CARDS.map((card, i) => (
          <Card key={card.title} card={card} visible={frame > cardStart + i * cardStagger} />
        ))}
      </div>
    </div>
  );
};
