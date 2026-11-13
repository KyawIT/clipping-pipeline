import React from 'react';
import {AbsoluteFill, useVideoConfig} from 'remotion';
import {fontFamily} from './fonts';
import {ACCENT, parseWords} from './Hook';

/** Thumbnail overlay: vignette + huge tilted stroked text, *starred* words in the accent colour. */
export const Thumb: React.FC<{text: string; y: number}> = ({text, y}) => {
  const {width, height} = useVideoConfig();
  const words = parseWords(text);
  const longest = Math.max(4, ...words.map((w) => w.t.length));
  // ponytail: width guess for Montserrat 900 caps (~0.8 em/char); measure text if long words still overflow
  const fontSize = Math.min(180, (width * 0.86) / (longest * 0.8));
  return (
    <>
      <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 42%, transparent 40%, rgba(0,0,0,0.7) 100%)'}} />
      <div
        style={{
          position: 'absolute',
          left: 0,
          width,
          top: height * y,
          transform: 'translateY(-50%) rotate(-4deg)',
          display: 'flex',
          flexWrap: 'wrap',
          justifyContent: 'center',
          gap: '0 28px',
          padding: '0 50px',
          boxSizing: 'border-box',
        }}
      >
        {words.map((w, i) => (
          <span
            key={i}
            style={{
              fontFamily,
              fontWeight: 900,
              fontSize,
              lineHeight: 1.02,
              textTransform: 'uppercase',
              color: w.hot ? ACCENT : 'white',
              WebkitTextStroke: w.emoji ? undefined : `${Math.round(fontSize * 0.14)}px black`,
              paintOrder: 'stroke fill',
              textShadow: '0 12px 34px rgba(0,0,0,0.75)',
            }}
          >
            {w.t}
          </span>
        ))}
      </div>
    </>
  );
};
