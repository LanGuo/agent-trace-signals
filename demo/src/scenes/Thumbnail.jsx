import { C } from "../colors";

const NODES = [
  { x: 130, y: 60 },
  { x: 330, y: 25 },
  { x: 330, y: 155 },
  { x: 530, y: 90 },
];
const EDGES = [
  [0, 1, C.blue], [0, 2, C.green], [1, 2, C.green], [1, 3, C.purple], [2, 3, C.purple],
];

export const Thumbnail = () => (
  <div style={{
    width: "100%", height: "100%",
    background: `radial-gradient(ellipse 1200px 700px at 28% 42%, #1c2740 0%, ${C.bg} 65%)`,
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "0 74px",
    fontFamily: "system-ui, sans-serif",
  }}>
    {/* Left: headline */}
    <div style={{ display: "flex", flexDirection: "column", maxWidth: 660 }}>
      <div style={{
        display: "inline-flex", alignItems: "center", gap: 10,
        background: `${C.blue}1c`, border: `1.5px solid ${C.blue}55`, borderRadius: 999,
        padding: "8px 18px", marginBottom: 28, width: "fit-content",
      }}>
        <span style={{ fontSize: 20 }}>🧠</span>
        <span style={{ color: C.blue, fontSize: 22, fontWeight: 700, letterSpacing: 0.5 }}>AGENT TRACE SIGNALS</span>
      </div>

      <div style={{ fontSize: 76, fontWeight: 800, color: C.white, lineHeight: 1.04, letterSpacing: -2 }}>
        Give Your<br/>Coding Agent<br/>a <span style={{ color: C.green }}>Memory</span>
      </div>

      <div style={{ marginTop: 26, fontSize: 25, color: C.dim, lineHeight: 1.5, fontWeight: 500 }}>
        Extraction · Clustering · Knowledge Graph · MCP
      </div>
    </div>

    {/* Right: graph illustration */}
    <div style={{ position: "relative", width: 560, height: 460, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <svg width="560" height="300" style={{ overflow: "visible" }}>
        {EDGES.map(([a, b, color], i) => (
          <line key={i} x1={NODES[a].x} y1={NODES[a].y} x2={NODES[b].x} y2={NODES[b].y}
            stroke={color} strokeWidth={4} opacity={0.9} />
        ))}
        {NODES.map((n, i) => (
          <circle key={i} cx={n.x} cy={n.y} r={30} fill={C.surface} stroke={C.white} strokeWidth={3} />
        ))}
      </svg>
      <div style={{
        position: "absolute", bottom: 6, left: "50%", transform: "translateX(-50%)",
        background: `${C.surface}`, border: `2px solid ${C.border}`, borderRadius: 14,
        padding: "16px 26px", fontFamily: "monospace", fontSize: 21, color: C.green,
        boxShadow: "0 12px 40px rgba(0,0,0,0.5)", whiteSpace: "nowrap",
      }}>
        ❯ ats recall "gemma4 vs gemma3"
      </div>
    </div>
  </div>
);
