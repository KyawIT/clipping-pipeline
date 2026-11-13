import {loadFont} from '@remotion/fonts';
import {staticFile} from 'remotion';

// Bundled locally (public/fonts, SIL OFL) so renders work offline and never stall on Google Fonts.
export const fontFamily = 'Montserrat';

loadFont({family: fontFamily, url: staticFile('fonts/montserrat-latin-800-normal.woff2'), weight: '800'});
loadFont({family: fontFamily, url: staticFile('fonts/montserrat-latin-900-normal.woff2'), weight: '900'});
