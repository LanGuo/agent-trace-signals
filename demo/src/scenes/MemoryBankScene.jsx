import { useCurrentFrame, interpolate } from "remotion";
import { C } from "../colors";

const TYPES = [
  { label: "Episodic",    sub: "1,279 resolved · 177 open", n: "1,458", color: C.blue },
  { label: "Strategy",    sub: "approach + tradeoffs", n: "1,007", color: C.purple },
  { label: "Preference",  sub: "user's stated wishes", n: "184", color: C.cyan },
  { label: "Procedural",  sub: "how it was done",   n: "180",   color: C.green },
  { label: "Recovery",    sub: "stuck → fixed",     n: "148",   color: C.red },
  { label: "Inefficiency", sub: "wasted effort",    n: "9",     color: C.orange },
];

const TypeCard = ({ t, visible }) => (
  <div style={{
    opacity: visible ? 1 : 0,
    transform: `translateY(${visible ? 0 : 12}px)`,
    transition: "all 0.25s",
    background: `${t.color}1c`,
    border: `1.5px solid ${t.color}66`,
    borderRadius: 10,
    padding: "18px 20px",
    width: 225,
  }}>
    <div style={{ fontFamily: "system-ui", fontSize: 25, fontWeight: 700, color: t.color, marginBottom: 4 }}>{t.label}</div>
    <div style={{ fontFamily: "system-ui", fontSize: 16.5, color: C.dim, marginBottom: 10 }}>{t.sub}</div>
    <div style={{ fontFamily: "monospace", fontSize: 28, fontWeight: 700, color: C.white }}>{t.n}</div>
  </div>
);

export const MemoryBankScene = () => {
  const frame = useCurrentFrame();
  const titleOpacity = interpolate(frame, [0, 15], [0, 1], { extrapolateRight: "clamp" });
  const subOpacity = interpolate(frame, [10, 30], [0, 1], { extrapolateRight: "clamp" });

  const gridStart = 35;
  const consolidateStart = gridStart + TYPES.length * 10 + 25;
  const footerStart = consolidateStart + 45;

  return (
    <div style={{
      width: "100%", height: "100%",
      background: C.bg,
      display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center",
      gap: 22,
      padding: "0 60px",
    }}>
      <div style={{ opacity: titleOpacity, fontFamily: "system-ui", fontSize: 48, fontWeight: 700, color: C.white }}>
        One Call, Six Signal Types
      </div>
      <div style={{ opacity: subOpacity, fontFamily: "system-ui", fontSize: 23, color: C.dim, textAlign: "center", maxWidth: 940 }}>
        Entities capture <em style={{ color: C.white, fontStyle: "normal" }}>nouns</em> — a single structured-output call per chunk also extracts <em style={{ color: C.white, fontStyle: "normal" }}>behavior</em>: memories and patterns, together.
      </div>

      <div style={{ display: "flex", gap: 16, marginTop: 8 }}>
        {TYPES.map((t, i) => (
          <TypeCard key={t.label} t={t} visible={frame > gridStart + i * 10} />
        ))}
      </div>

      {frame > consolidateStart && (
        <div style={{
          opacity: interpolate(frame, [consolidateStart, consolidateStart + 20], [0, 1], { extrapolateRight: "clamp" }),
          display: "flex", alignItems: "center", gap: 16, marginTop: 8,
        }}>
          <div style={{ color: C.border, fontSize: 32 }}>↓</div>
          <div style={{
            background: `${C.yellow}1c`, border: `1.5px solid ${C.yellow}66`, borderRadius: 10,
            padding: "14px 26px", fontFamily: "system-ui", fontSize: 21, color: C.white,
          }}>
            Two-level consolidation (within-session → cross-session) → <span style={{ color: C.yellow, fontWeight: 700 }}>250 cluster memories</span> (148 within-session + 102 cross-session) — recurring themes merged, not duplicated
          </div>
        </div>
      )}

      {frame > footerStart && (
        <div style={{
          opacity: interpolate(frame, [footerStart, footerStart + 20], [0, 1], { extrapolateRight: "clamp" }),
          fontFamily: "system-ui", fontSize: 20, color: C.dim, textAlign: "center", maxWidth: 940, marginTop: 4,
        }}>
          The taxonomy gets the same scrutiny as the pipeline — a "timeless fact" type was cut after audits showed the worst error rate of any type.
        </div>
      )}
    </div>
  );
};
