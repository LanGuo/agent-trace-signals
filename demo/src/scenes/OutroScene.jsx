import { useCurrentFrame, interpolate } from "remotion";
import { C } from "../colors";

const FEATURES = [
  "Claude Code · Gemini CLI · OpenCode ingestion",
  "One combined call — entities, memories, behavioral patterns",
  "Dual embeddings (semantic + structural) → automatic session & memory clustering",
  "Knowledge graph — workspace, structural, memory, similarity edges",
  "Hybrid BM25 + semantic retrieval (RRF) · Web UI · MCP · CLI",
  "Consolidation with guardrails — conflict detection, supersession, retrieval tracking",
];

const STACK = "Claude Code / Gemini CLI / OpenCode · Ollama + nomic-embed-text · Ollama + gemma3:12b · SQLite + sqlite-vec · BM25 (FTS5) + RRF · Streamlit + Plotly · HDBSCAN";

export const OutroScene = () => {
  const frame = useCurrentFrame();

  const titleOpacity = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: "clamp" });
  const stackOpacity = interpolate(frame, [118, 138], [0, 1], { extrapolateRight: "clamp" });
  const repoOpacity  = interpolate(frame, [148, 168], [0, 1], { extrapolateRight: "clamp" });

  return (
    <div style={{
      width: "100%", height: "100%",
      background: `radial-gradient(ellipse at 50% 40%, #1a2332 0%, ${C.bg} 70%)`,
      display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center",
      gap: 32,
    }}>
      <div style={{ opacity: titleOpacity, fontSize: 62, }}>🧠</div>
      <div style={{ opacity: titleOpacity, textAlign: "center" }}>
        <div style={{ fontFamily: "system-ui", fontSize: 62, fontWeight: 700, color: C.white, letterSpacing: -1 }}>
          Agent Trace Signals
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 4 }}>
        {FEATURES.map((f, i) => (
          <div key={i} style={{
            opacity: interpolate(frame, [20 + i*16, 35 + i*16], [0, 1], { extrapolateRight: "clamp" }),
            display: "flex", alignItems: "center", gap: 12,
            fontFamily: "system-ui", fontSize: 23, color: C.dim,
          }}>
            <span style={{ color: C.green }}>✓</span> {f}
          </div>
        ))}
      </div>

      <div style={{
        opacity: stackOpacity,
        fontFamily: "system-ui", fontSize: 18, color: C.dim, textAlign: "center",
        maxWidth: 1000, lineHeight: 1.6, marginTop: 4,
      }}>
        {STACK}
      </div>

      <div style={{
        opacity: repoOpacity,
        marginTop: 16,
        background: C.surface,
        border: `1px solid ${C.border}`,
        borderRadius: 10,
        padding: "16px 32px",
        fontFamily: "monospace", fontSize: 27, color: C.blue,
      }}>
        github.com/LanGuo/agent-trace-signals
      </div>
    </div>
  );
};
