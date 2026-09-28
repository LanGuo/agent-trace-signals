import { useCurrentFrame, interpolate, spring, useVideoConfig } from "remotion";
import { C } from "../colors";

export const TitleScene = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleOpacity = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: "clamp" });
  const subtitleOpacity = interpolate(frame, [25, 45], [0, 1], { extrapolateRight: "clamp" });
  const taglineOpacity = interpolate(frame, [50, 70], [0, 1], { extrapolateRight: "clamp" });
  const titleY = interpolate(frame, [0, 20], [30, 0], { extrapolateRight: "clamp" });

  return (
    <div style={{
      width: "100%", height: "100%",
      background: `radial-gradient(ellipse at 50% 40%, #1a2332 0%, ${C.bg} 70%)`,
      display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center",
      gap: 24,
    }}>
      {/* Logo / icon */}
      <div style={{
        opacity: titleOpacity,
        transform: `translateY(${titleY}px)`,
        fontSize: 80,
        marginBottom: 8,
      }}>🧠</div>

      <div style={{ opacity: titleOpacity, transform: `translateY(${titleY}px)`, textAlign: "center" }}>
        <div style={{
          fontFamily: "system-ui, sans-serif",
          fontSize: 80,
          fontWeight: 700,
          color: C.white,
          letterSpacing: -2,
        }}>
          Agent Trace Signals
        </div>
      </div>

      <div style={{ opacity: subtitleOpacity, textAlign: "center" }}>
        <div style={{
          fontFamily: "system-ui, sans-serif",
          fontSize: 34,
          color: C.blue,
          fontWeight: 500,
        }}>
          A memory and learning layer for coding agents
        </div>
      </div>

      <div style={{ opacity: taglineOpacity, textAlign: "center", marginTop: 16 }}>
        <div style={{
          fontFamily: "system-ui, sans-serif",
          fontSize: 25,
          color: C.dim,
          maxWidth: 820,
          lineHeight: 1.5,
        }}>
          Every agent session starts from zero — no matter how much was already learned.<br/>
          ATS captures <em style={{ color: C.white, fontStyle: "normal" }}>how</em> you work, not just what you built, and gives it back.
        </div>
      </div>
    </div>
  );
};
