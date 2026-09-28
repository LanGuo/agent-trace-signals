import { useCurrentFrame, interpolate } from "remotion";
import { C } from "../colors";

const STEPS = [
  { label: "Traces", sub: "Claude Code\nGemini CLI\nOpenCode", color: C.dim, icon: "🗂️" },
  { label: "Ingest", sub: "1 combined LLM call/chunk\n→ summary + entities\n+ memories + patterns", color: C.blue, icon: "📥" },
  { label: "Embed", sub: "Semantic (content) +\nstructural (raw sampled\ntrace text)", color: C.purple, icon: "🔢" },
  { label: "Cluster", sub: "Group similar sessions +\nconsolidate recurring\nmemories across sessions", color: C.green, icon: "🕸️" },
  { label: "Retrieve", sub: "Hybrid BM25 + semantic\n(RRF fusion) + graph hops", color: C.orange, icon: "🔍" },
  { label: "Serve", sub: "MCP server →\nback into your\ncoding agent", color: C.cyan, icon: "📡" },
];

const Node = ({ step, visible, active }) => (
  <div style={{
    display: "flex", flexDirection: "column", alignItems: "center",
    opacity: visible ? 1 : 0,
    transition: "opacity 0.3s",
    width: 205,
  }}>
    <div style={{
      width: 80, height: 80, borderRadius: 16,
      background: active ? step.color + "33" : C.surface,
      border: `2px solid ${active ? step.color : C.border}`,
      display: "flex", alignItems: "center", justifyContent: "center",
      fontSize: 36,
      boxShadow: active ? `0 0 24px ${step.color}44` : "none",
      marginBottom: 10,
    }}>{step.icon}</div>
    <div style={{
      fontFamily: "monospace", fontSize: 21, color: active ? step.color : C.dim,
      fontWeight: 700, marginBottom: 8, textAlign: "center",
    }}>{step.label}</div>
    <div style={{
      fontFamily: "system-ui", fontSize: 16.5, color: C.dim,
      textAlign: "center", lineHeight: 1.45, whiteSpace: "pre",
    }}>{step.sub}</div>
  </div>
);

const Arrow = ({ visible }) => (
  <div style={{
    opacity: visible ? 1 : 0,
    color: C.border, fontSize: 32, alignSelf: "flex-start", marginTop: 24,
    paddingBottom: 92,
  }}>→</div>
);

export const PipelineScene = () => {
  const frame = useCurrentFrame();
  const visibleCount = Math.floor(frame / 18) + 1;
  const activeIdx = Math.min(visibleCount - 1, STEPS.length - 1);

  const titleOpacity = interpolate(frame, [0, 15], [0, 1], { extrapolateRight: "clamp" });
  const noteStart = STEPS.length * 18 + 10;
  const statsStart = noteStart + 30;

  return (
    <div style={{
      width: "100%", height: "100%",
      background: C.bg,
      display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center",
      gap: 36,
    }}>
      <div style={{ opacity: titleOpacity, fontFamily: "system-ui", fontSize: 48, fontWeight: 700, color: C.white }}>
        The Pipeline
      </div>

      <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
        {STEPS.map((step, i) => (
          <>
            <Node key={step.label} step={step} visible={i < visibleCount} active={i === activeIdx} />
            {i < STEPS.length - 1 && <Arrow key={`arrow-${i}`} visible={i < visibleCount - 1} />}
          </>
        ))}
      </div>

      {frame > noteStart && (
        <div style={{
          opacity: interpolate(frame, [noteStart, noteStart + 15], [0, 1], { extrapolateRight: "clamp" }),
          fontFamily: "system-ui", fontSize: 22, color: C.dim, textAlign: "center", maxWidth: 1020,
        }}>
          One structured-output call replaced four heuristic passes (regex + GLiNER + verifier + memory extractor) — <span style={{ color: C.white }}>~half the LLM calls</span>, higher-quality output.
        </div>
      )}

      {frame > statsStart && (
        <div style={{
          display: "flex", gap: 48, marginTop: 4,
          opacity: interpolate(frame, [statsStart, statsStart + 18], [0, 1], { extrapolateRight: "clamp" }),
        }}>
          {[
            { n: "103", label: "sessions" },
            { n: "1,176", label: "chunks" },
            { n: "1,744", label: "entities" },
            { n: "3,236", label: "memories" },
          ].map(({ n, label }) => (
            <div key={label} style={{ textAlign: "center" }}>
              <div style={{ fontFamily: "system-ui", fontSize: 44, fontWeight: 700, color: C.blue }}>{n}</div>
              <div style={{ fontFamily: "system-ui", fontSize: 18, color: C.dim }}>{label}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
