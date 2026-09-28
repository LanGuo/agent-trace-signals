import { Composition } from "remotion";
import { AtsDemo } from "./AtsDemo";
import { Thumbnail } from "./scenes/Thumbnail";

export const RemotionRoot = () => (
  <>
    <Composition
      id="AtsDemo"
      component={AtsDemo}
      durationInFrames={2420} // ~81 seconds @ 30fps
      fps={30}
      width={1920}
      height={1080}
    />
    <Composition
      id="Thumbnail"
      component={Thumbnail}
      durationInFrames={1}
      fps={30}
      width={1280}
      height={720}
    />
  </>
);
