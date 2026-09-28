import { useCurrentFrame, interpolate } from "remotion";
import { C } from "../colors";
import { TerminalWindow, TypewriterLine, Prompt } from "../Terminal";

// Real output from `ats recall "gemma4 vs gemma3 model comparison extraction audit"`,
// surfacing memories from the actual July 15 model-audit sessions in this repo's own history.
const RECALL_RESULTS = [
  { score: "0.0320", type: "episodic",   content: "Model comparison revealed that gemma4:e4b successfully extracted a user preference stating that advanced analysis method..." },
  { score: "0.0313", type: "episodic",   content: "The comparison of model outputs revealed a significant difference in preference extraction capability: gemma4:e4b succes..." },
  { score: "0.0306", type: "preference", content: "The user prefers memories/patterns extracted by gemma4-e4b over gemma3-12b, while noting that gemma3-12b achieved better..." },
  { score: "0.0301", type: "episodic",   content: "A local-model comparison process (using gemma3:12b, gemma4:e4b, gemma4:12b, olmo-3:7b-think) is currently running in the..." },
];

const ResultRow = ({ r, visible }) => (
  <div style={{
    opacity: visible ? 1 : 0,
    transform: `translateX(${visible ? 0 : 20}px)`,
    transition: "all 0.2s",
    display: "flex", gap: 16, alignItems: "flex-start",
    padding: "8px 0",
    borderBottom: `1px solid ${C.border}22`,
  }}>
    <span style={{ color: C.yellow, fontFamily: "monospace", fontSize: 22, minWidth: 78 }}>{r.score}</span>
    <span style={{ color: C.purple, fontFamily: "monospace", fontSize: 20, minWidth: 160 }}>{r.type}</span>
    <span style={{ color: C.white, fontFamily: "system-ui", fontSize: 20, lineHeight: 1.4 }}>{r.content}</span>
  </div>
);

export const RetrievalScene = () => {
  const frame = useCurrentFrame();

  const titleOpacity = interpolate(frame, [0, 15], [0, 1], { extrapolateRight: "clamp" });

  const cmd1Start = 5;
  const results1Start = 45;
  const noteStart = results1Start + RECALL_RESULTS.length * 12 + 20;

  const mcpStart = noteStart + 55;
  const mcpTextStart = mcpStart + 5;
  const mcpOutStart = mcpStart + 55;

  return (
    <div style={{
      width: "100%", height: "100%",
      background: C.bg,
      display: "flex", flexDirection: "column",
      padding: "44px 80px",
      gap: 20,
    }}>
      <div style={{ opacity: titleOpacity, fontFamily: "system-ui", fontSize: 44, fontWeight: 700, color: C.white, marginBottom: 4 }}>
        Hybrid Retrieval, Fed Back Through MCP
      </div>

      <TerminalWindow title="ats — agent trace signals">
        <div style={{ display: "flex", alignItems: "center" }}>
          <Prompt />
          <TypewriterLine text='ats recall "gemma4 vs gemma3 extraction audit"' startFrame={cmd1Start} color={C.white} />
        </div>

        {frame > results1Start && (
          <div style={{ marginTop: 12 }}>
            {RECALL_RESULTS.map((r, i) => (
              <ResultRow key={i} r={r} visible={frame > results1Start + i * 12} />
            ))}
          </div>
        )}

        {frame > noteStart && (
          <div style={{
            opacity: interpolate(frame, [noteStart, noteStart + 15], [0, 1], { extrapolateRight: "clamp" }),
            fontFamily: "system-ui", fontSize: 19, color: C.dim, marginTop: 14, lineHeight: 1.6,
          }}>
            Lexical (BM25 / FTS5) + semantic (vec0 cosine) fused via <span style={{ color: C.white }}>RRF</span> — all methods return comparable 0–0.033 scores.
          </div>
        )}
      </TerminalWindow>

      {frame > mcpStart && (
        <TerminalWindow title="Claude Code — mcp: ats-memory" style={{ opacity: interpolate(frame, [mcpStart, mcpStart + 15], [0, 1], { extrapolateRight: "clamp" }) }}>
          <div style={{ display: "flex", alignItems: "center" }}>
            <Prompt />
            <TypewriterLine text="Use the ats-memory recall tool: which model did we settle on for extraction?" startFrame={mcpTextStart} color={C.white} />
          </div>
          {frame > mcpOutStart && (
            <div style={{
              opacity: interpolate(frame, [mcpOutStart, mcpOutStart + 15], [0, 1], { extrapolateRight: "clamp" }),
              fontFamily: "system-ui", fontSize: 19, color: C.dim, marginTop: 10, lineHeight: 1.6,
            }}>
              Any coding agent can query its own accumulated history <span style={{ color: C.white }}>mid-session</span> — the loop closes.
            </div>
          )}
        </TerminalWindow>
      )}
    </div>
  );
};
