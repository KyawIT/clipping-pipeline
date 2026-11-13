export type Box = {x: number; y: number; w: number; h: number};

export type CaptionWord = {t: string; s: number; e: number};
export type CaptionPage = {start: number; end: number; words: CaptionWord[]};

export type Layout = 'split' | 'fullcam' | 'fullgame' | 'podcast_follow' | 'podcast_stack' | 'center';

export type ClipProps = {
  src: string; // path inside remotion/public
  durationSec: number;
  fps: number;
  width: number;
  height: number;
  sourceWidth: number;
  sourceHeight: number;
  layout: Layout;
  hook: string;
  hookSeconds: number;
  splitCamRatio: number;
  captions: CaptionPage[];
  cam: Box | null;
  game: Box | null;
  follow: {t: number; cx: number}[] | null;
  stack: Box[] | null;
  emphasis?: string[]; // caption words that pop
  thumbText?: string; // set -> render the thumbnail look instead of hook + captions
};
