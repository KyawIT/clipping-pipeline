import React from 'react';
import {Easing, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {fontFamily} from './fonts';

export const ACCENT = '#FFE500';
export const outExpo = Easing.bezier(0.16, 1, 0.3, 1);
export const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

/** "He did *NOT* expect this" -> words; *starred* words are hot (accent colour). */
export const parseWords = (text: string) =>
  text.split(/\s+/).filter(Boolean).map((raw) => {
    const t = raw.replace(/\*/g, '');
    return {t, hot: raw.includes('*'), emoji: !/[\p{L}\p{N}]/u.test(t)};
  });

/**
 * Hook text at the top for the first few seconds. Words crash in one by one from oversize (3 f apart,
 * expo-out, no bounce); hot words get a highlighter bar wiped in behind them. Leaves by whipping up.
 */
export const Hook: React.FC<{text: string; seconds: number}> = ({text, seconds}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const end = Math.round(seconds * fps);
  if (!text || frame > end) return null;

  const leave = interpolate(frame, [end - 7, end], [0, 1], {...clamp, easing: Easing.in(Easing.quad)});

  return (
    <div
      style={{
        position: 'absolute',
        top: height * 0.07,
        left: 0,
        width,
        display: 'flex',
        justifyContent: 'center',
        transform: `translateY(${-leave * height * 0.3}px) scale(${1 - 0.1 * leave})`,
        filter: leave > 0 ? `blur(${leave * 10}px)` : undefined,
      }}
    >
      <div style={{maxWidth: width * 0.88, display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: '8px 22px'}}>
        {parseWords(text).map((w, i) => {
          const f = frame - (2 + i * 3);
          const p = interpolate(f, [0, 7], [0, 1], {...clamp, easing: outExpo});
          const bar = interpolate(f, [3, 10], [0, 1], {...clamp, easing: outExpo});
          const tilt = ((i * 37) % 5) - 2; // -2..2 deg, sticker feel
          const onBar = w.hot && bar > 0.4;
          return (
            <span
              key={i}
              style={{
                position: 'relative',
                display: 'inline-block',
                visibility: f < 0 ? 'hidden' : 'visible', // keeps the layout stable while words arrive
                transform: `scale(${2.6 - 1.6 * p}) rotate(${tilt * p}deg)`,
                filter: p < 1 ? `blur(${(1 - p) * 8}px)` : undefined,
                fontFamily,
                fontWeight: 900,
                fontSize: 66,
                lineHeight: 1.15,
              }}
            >
              {w.hot ? (
                <span style={{position: 'absolute', inset: '4px -12px', background: ACCENT, borderRadius: 10,
                  transformOrigin: 'left', transform: `scaleX(${bar})`}} />
              ) : null}
              <span
                style={{
                  position: 'relative',
                  color: onBar ? 'black' : w.hot ? ACCENT : 'white',
                  WebkitTextStroke: onBar || w.emoji ? undefined : '12px black',
                  paintOrder: 'stroke fill',
                  textShadow: onBar ? undefined : '0 6px 20px rgba(0,0,0,0.6)',
                }}
              >
                {w.t}
              </span>
            </span>
          );
        })}
      </div>
    </div>
  );
};
