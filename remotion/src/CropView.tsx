import React from 'react';
import {OffthreadVideo, staticFile} from 'remotion';
import type {Box} from './types';

type Props = {
  src: string;
  box: Box; // crop region in the source, normalized 0-1
  sourceWidth: number;
  sourceHeight: number;
  width: number; // output size of this view in px
  height: number;
  fit?: 'cover' | 'contain';
  muted?: boolean;
  style?: React.CSSProperties;
  videoStyle?: React.CSSProperties;
};

/**
 * Shows the region `box` of the source video inside a width x height view.
 * cover: fills the view (crops the box further around its center if aspect ratios differ).
 * contain: shows the whole box, letterboxed.
 */
export const CropView: React.FC<Props> = ({
  src, box, sourceWidth, sourceHeight, width, height, fit = 'cover', muted = true, style, videoStyle,
}) => {
  const boxW = Math.max(1, box.w * sourceWidth);
  const boxH = Math.max(1, box.h * sourceHeight);
  const scale = fit === 'cover' ? Math.max(width / boxW, height / boxH) : Math.min(width / boxW, height / boxH);
  const vw = sourceWidth * scale;
  const vh = sourceHeight * scale;
  const cx = (box.x + box.w / 2) * sourceWidth * scale;
  const cy = (box.y + box.h / 2) * sourceHeight * scale;
  let left = width / 2 - cx;
  let top = height / 2 - cy;
  if (fit === 'cover') {
    // never reveal empty space outside the source frame
    left = Math.min(0, Math.max(width - vw, left));
    top = Math.min(0, Math.max(height - vh, top));
  }
  return (
    <div style={{position: 'relative', width, height, overflow: 'hidden', ...style}}>
      <OffthreadVideo
        src={staticFile(src)}
        muted={muted}
        style={{position: 'absolute', left, top, width: vw, height: vh, maxWidth: 'none', ...videoStyle}}
      />
    </div>
  );
};
