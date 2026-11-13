import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {fontFamily} from './fonts';
import {ACCENT, clamp, outExpo} from './Hook';
import type {CaptionPage} from './types';

const norm = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}']/gu, '');

/**
 * Word-by-word captions: each word lands from oversize the moment it is spoken (5 f, expo-out).
 * The spoken word is yellow; emphasis words stay yellow, land bigger and give a short decaying shake.
 */
export const Captions: React.FC<{pages: CaptionPage[]; y: number; emphasis?: string[]}> = ({pages, y, emphasis = []}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const t = frame / fps;
  const page = pages.find((p) => t >= p.start && t < p.end);
  if (!page) return null;
  const hot = new Set(emphasis.map(norm));

  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        width,
        top: height * y,
        transform: 'translateY(-50%)',
        display: 'flex',
        justifyContent: 'center',
        flexWrap: 'wrap',
        gap: '0 34px',
        padding: '0 60px',
        boxSizing: 'border-box',
        textAlign: 'center',
      }}
    >
      {page.words.map((w, i) => {
        const f = (t - w.s) * fps;
        const isHot = hot.has(norm(w.t));
        const active = t >= w.s && t < w.e + 0.05;
        const p = interpolate(f, [0, 5], [0, 1], {...clamp, easing: outExpo});
        const [from, land] = isHot ? [1.9, 1.22] : [1.3, active ? 1.06 : 1];
        const shake = isHot ? Math.sin(f * 2.2) * 5 * (1 - interpolate(f, [0, 10], [0, 1], clamp)) : 0;
        return (
          <span
            key={i}
            style={{
              fontFamily,
              fontWeight: 900,
              fontSize: 92,
              lineHeight: 1.1,
              textTransform: 'uppercase',
              color: active || isHot ? ACCENT : 'white',
              WebkitTextStroke: '14px black',
              paintOrder: 'stroke fill',
              textShadow: '0 6px 18px rgba(0,0,0,0.55)',
              display: 'inline-block',
              visibility: f < -0.6 ? 'hidden' : 'visible', // not spoken yet: holds its place
              transform: `translateY(${(1 - p) * 28}px) scale(${from + (land - from) * p}) rotate(${shake}deg)`,
            }}
          >
            {w.t.replace(/^[,.;:]+|[,;:]+$/g, '')}
          </span>
        );
      })}
    </div>
  );
};
