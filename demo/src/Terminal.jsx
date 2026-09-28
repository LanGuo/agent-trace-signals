import { useCurrentFrame, interpolate, spring, useVideoConfig } from "remotion";
import { C } from "./colors";

// Renders a terminal line with typewriter effect starting at `startFrame`
export const TypewriterLine = ({ text, startFrame, color = C.white, fps = 30 }) => {
  const frame = useCurrentFrame();
  const elapsed = Math.max(0, frame - startFrame);
  const charsPerFrame = 2.5;
  const chars = Math.min(text.length, Math.floor(elapsed * charsPerFrame));
  const visible = frame >= startFrame;
  if (!visible) return null;
  return (
    <div style={{ color, fontFamily: "monospace", fontSize: 27, lineHeight: 1.7, whiteSpace: "pre" }}>
      {text.slice(0, chars)}
      {chars < text.length && <span style={{ opacity: (Math.floor(frame / 15)) % 2 === 0 ? 1 : 0 }}>█</span>}
    </div>
  );
};

export const TerminalWindow = ({ title = "Terminal", children, style }) => (
  <div style={{
    background: C.surface,
    border: `1px solid ${C.border}`,
    borderRadius: 10,
    overflow: "hidden",
    boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
    ...style,
  }}>
    {/* Title bar */}
    <div style={{
      background: C.bg,
      padding: "10px 16px",
      display: "flex",
      alignItems: "center",
      gap: 8,
      borderBottom: `1px solid ${C.border}`,
    }}>
      {["#f85149", "#e3b341", "#3fb950"].map((c, i) => (
        <div key={i} style={{ width: 12, height: 12, borderRadius: 6, background: c }} />
      ))}
      <span style={{ color: C.dim, fontFamily: "monospace", fontSize: 17, marginLeft: 8 }}>{title}</span>
    </div>
    <div style={{ padding: "20px 24px" }}>{children}</div>
  </div>
);

// A prompt prefix
export const Prompt = () => (
  <span style={{ color: C.green, fontFamily: "monospace", fontSize: 27 }}>❯ </span>
);
