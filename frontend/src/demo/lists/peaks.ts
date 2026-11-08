import type { ListHandler } from './index';
import { NO_WAVEFORM_TEXT } from '../strings';

type PeaksBody = { resolutionMs: number; peaks: number[] };

// The recorder keeps one whole-episode waveform per episode under the bare path; the editor asks for
// zoom windows, so slice the recording to the requested start and end.
export const peaksHandler: ListHandler = async (url, load) => {
  const path = /\/feeds\/[^/]+\/episodes\/[^/]+\/peaks$/.exec(url.pathname)?.[0];
  const whole = path ? await load(path) : null;
  if (!whole) return { status: 404, body: { error: NO_WAVEFORM_TEXT } };
  const body = whole.body as PeaksBody;
  const perSecond = 1000 / body.resolutionMs;
  const p = url.searchParams;
  if ([p.get('start'), p.get('end')].some((v) => v !== null && !Number.isFinite(Number(v)))) {
    return { status: 400, body: { error: 'start and end must be numbers' } };
  }
  const first = Math.min(body.peaks.length, Math.floor(Math.max(0, Number(p.get('start')) || 0) * perSecond));
  const last = p.has('end')
    ? Math.min(body.peaks.length, Math.max(first, Math.ceil(Number(p.get('end')) * perSecond)))
    : body.peaks.length;
  return { status: 200, body: { ...body, start: first / perSecond, end: last / perSecond, peaks: body.peaks.slice(first, last) } };
};
