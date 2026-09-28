import { AbsoluteFill, Sequence, useCurrentFrame, interpolate } from "remotion";
import { TitleScene }      from "./scenes/TitleScene";
import { PipelineScene }   from "./scenes/PipelineScene";
import { MemoryBankScene } from "./scenes/MemoryBankScene";
import { MemoryQualityScene } from "./scenes/MemoryQualityScene";
import { GraphScene }      from "./scenes/GraphScene";
import { RetrievalScene }  from "./scenes/RetrievalScene";
import { EvaluationScene } from "./scenes/EvaluationScene";
import { ResearchScene }   from "./scenes/ResearchScene";
import { OutroScene }      from "./scenes/OutroScene";

// Sequence wraps auto-offset useCurrentFrame() for child components
// Cross-fade wrapper: fades in at start, fades out near end
const FadeSequence = ({ from, duration, children }) => {
  const FADE = 15;
  return (
    <Sequence from={from} durationInFrames={duration + FADE}>
      <FadeInOut duration={duration} fadeFrames={FADE}>
        {children}
      </FadeInOut>
    </Sequence>
  );
};

const FadeInOut = ({ duration, fadeFrames, children }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(
    frame,
    [0, fadeFrames, duration - fadeFrames, duration],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );
  return <AbsoluteFill style={{ opacity }}>{children}</AbsoluteFill>;
};

export const AtsDemo = () => (
  <AbsoluteFill style={{ background: "#0d1117" }}>
    <FadeSequence from={0}    duration={160}><TitleScene /></FadeSequence>
    <FadeSequence from={150}  duration={240}><PipelineScene /></FadeSequence>
    <FadeSequence from={380}  duration={300}><MemoryBankScene /></FadeSequence>
    <FadeSequence from={670}  duration={260}><MemoryQualityScene /></FadeSequence>
    <FadeSequence from={920}  duration={260}><GraphScene /></FadeSequence>
    <FadeSequence from={1170} duration={380}><RetrievalScene /></FadeSequence>
    <FadeSequence from={1540} duration={280}><EvaluationScene /></FadeSequence>
    <FadeSequence from={1810} duration={340}><ResearchScene /></FadeSequence>
    <FadeSequence from={2140} duration={260}><OutroScene /></FadeSequence>
  </AbsoluteFill>
);
