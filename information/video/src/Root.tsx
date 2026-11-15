import { Composition } from "remotion";
import { JobAutoFlow, FPS, TOTAL } from "./Video";

export const RemotionRoot = () => {
  return (
    <Composition
      id="JobAutoFlow"
      component={JobAutoFlow}
      durationInFrames={TOTAL}
      fps={FPS}
      width={1920}
      height={1080}
    />
  );
};
