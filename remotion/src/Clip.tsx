import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {CropView} from './CropView';
import {Captions} from './Captions';
import {Hook} from './Hook';
import {Thumb} from './Thumb';
import type {Box, ClipProps} from './types';

const FULL: Box = {x: 0, y: 0, w: 1, h: 1};

/** Crop box with a 9:16 output aspect, centered at horizontal position cx. */
const verticalBox = (cx: number, sw: number, sh: number, outW: number, outH: number): Box => {
  const w = Math.min(1, (sh * (outW / outH)) / sw);
  const x = Math.min(1 - w, Math.max(0, cx - w / 2));
  return {x, y: 0, w, h: 1};
};

export const Clip: React.FC<ClipProps> = (p) => {
  const frame = useCurrentFrame();
  const {fps, width: W, height: H} = useVideoConfig();
  const t = frame / fps;
  const common = {src: p.src, sourceWidth: p.sourceWidth, sourceHeight: p.sourceHeight};
  const cam = p.cam ?? FULL;
  const game = p.game ?? FULL;

  let body: React.ReactNode;
  let captionY = 0.62; // vertical caption position (fraction of height)

  switch (p.layout) {
    case 'split': {
      const camH = Math.round(H * p.splitCamRatio);
      body = (
        <>
          <CropView {...common} box={cam} width={W} height={camH} muted />
          <CropView {...common} box={game} width={W} height={H - camH} muted={false} />
        </>
      );
      captionY = p.splitCamRatio + 0.06; // just below the seam, on top of the gameplay
      break;
    }
    case 'fullcam': {
      body = <CropView {...common} box={cam} width={W} height={H} muted={false} />;
      break;
    }
    case 'fullgame': {
      // blurred gameplay background + full-width gameplay + webcam bubble above it
      const gameH = Math.round((W * (game.h * p.sourceHeight)) / (game.w * p.sourceWidth));
      const gameTop = Math.round(H * 0.42 - gameH / 2);
      const bubble = Math.round(W * 0.42);
      body = (
        <>
          <AbsoluteFill>
            <CropView {...common} box={game} width={W} height={H} muted
              videoStyle={{filter: 'blur(28px) brightness(0.55)', transform: 'scale(1.1)'}} />
          </AbsoluteFill>
          <div style={{position: 'absolute', top: gameTop, left: 0}}>
            <CropView {...common} box={game} width={W} height={gameH} muted={false} />
          </div>
          {p.cam ? (
            <div style={{position: 'absolute', top: Math.max(40, gameTop - bubble - 30), left: (W - bubble) / 2}}>
              <CropView {...common} box={cam} width={bubble} height={bubble} muted
                style={{borderRadius: 36, border: '6px solid white', boxShadow: '0 10px 40px rgba(0,0,0,0.5)'}} />
            </div>
          ) : null}
        </>
      );
      captionY = Math.min(0.85, (gameTop + gameH) / H + 0.08);
      break;
    }
    case 'podcast_stack': {
      const [a, b] = p.stack ?? [FULL, FULL];
      body = (
        <>
          <CropView {...common} box={a} width={W} height={H / 2} muted={false} />
          <CropView {...common} box={b} width={W} height={H / 2} muted />
        </>
      );
      captionY = 0.5;
      break;
    }
    case 'podcast_follow': {
      const keys = p.follow && p.follow.length ? p.follow : [{t: 0, cx: 0.5}];
      const cx = keys.length === 1
        ? keys[0].cx
        : interpolate(t, keys.map((k) => k.t), keys.map((k) => k.cx), {
            extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
          });
      body = <CropView {...common} box={verticalBox(cx, p.sourceWidth, p.sourceHeight, W, H)} width={W} height={H} muted={false} />;
      captionY = 0.7;
      break;
    }
    case 'center':
    default: {
      body = <CropView {...common} box={verticalBox(0.5, p.sourceWidth, p.sourceHeight, W, H)} width={W} height={H} muted={false} />;
      captionY = 0.7;
    }
  }

  if (p.thumbText) {
    return (
      <AbsoluteFill style={{backgroundColor: 'black'}}>
        <div style={{display: 'flex', flexDirection: 'column', width: W, height: H,
          filter: 'contrast(1.12) saturate(1.35) brightness(1.05)'}}>{body}</div>
        <Thumb text={p.thumbText} y={Math.min(0.68, captionY)} />
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill style={{backgroundColor: 'black'}}>
      <div style={{display: 'flex', flexDirection: 'column', width: W, height: H}}>{body}</div>
      <Captions pages={p.captions} y={captionY} emphasis={p.emphasis} />
      <Hook text={p.hook} seconds={p.hookSeconds} />
    </AbsoluteFill>
  );
};
