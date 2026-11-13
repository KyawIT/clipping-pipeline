import {Composition} from 'remotion';
import {Clip} from './Clip';
import type {ClipProps} from './types';

// Sample props so `npm run studio` opens something. Real renders pass --props=<file>.
const defaultProps: ClipProps = {
  src: 'sample.mp4',
  durationSec: 8,
  fps: 30,
  width: 1080,
  height: 1920,
  sourceWidth: 1920,
  sourceHeight: 1080,
  layout: 'split',
  hook: 'He did NOT expect this to happen',
  hookSeconds: 3,
  splitCamRatio: 0.38,
  captions: [
    {start: 0.2, end: 1.2, words: [{t: 'wait', s: 0.2, e: 0.5}, {t: 'what', s: 0.5, e: 0.8}, {t: 'just', s: 0.8, e: 1.2}]},
    {start: 1.2, end: 2.4, words: [{t: 'happened', s: 1.2, e: 1.8}, {t: 'chat?!', s: 1.8, e: 2.4}]},
  ],
  cam: {x: 0.72, y: 0.02, w: 0.26, h: 0.3},
  game: {x: 0, y: 0, w: 1, h: 1},
  follow: null,
  stack: null,
};

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Clip"
      component={Clip}
      durationInFrames={240}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={defaultProps}
      calculateMetadata={({props}) => ({
        durationInFrames: Math.max(1, Math.ceil(props.durationSec * props.fps)),
        fps: props.fps,
        width: props.width,
        height: props.height,
      })}
    />
  );
};
