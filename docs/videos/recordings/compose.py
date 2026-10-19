import json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# compose.py beats/<video>.json <portrait|landscape> [beat indexes]
# Frames each clip in a phone or laptop shape, adds the caption, joins the beats with crossfades.
BEATS_FILE, MODE = sys.argv[1:3]
ONLY = sys.argv[3:]
D = os.path.dirname(os.path.abspath(__file__))
SPEC = json.load(open(BEATS_FILE))
VIDEO = os.path.splitext(os.path.basename(BEATS_FILE))[0]
CLIPS = f'{D}/clips/{VIDEO}'
FINAL = f"{D}/out/{SPEC['output']}{'-landscape' if MODE == 'landscape' else ''}.mp4"
OUT = f'{D}/work-{MODE}-{VIDEO}'; os.makedirs(OUT, exist_ok=True); os.makedirs(f'{D}/out', exist_ok=True)
FPS = 30
XF = 0.35  # crossfade between beats, seconds
DEVICES = json.load(open(f'{D}/devices.json'))
BG = (21, 24, 29); FG = (243, 245, 247); MUTED = (154, 163, 173); TEAL = (0, 201, 167); BORDER = (58, 64, 73)
F_BOLD = '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf'
F_REG = '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'
if MODE == 'portrait':
    W, H = 1080, 1920
    PHONE = dict(w=700, h=1515, x=190, y=345, r=64)
    DESK = dict(w=1000, h=1250, x=40, y=380, r=40)
else:
    W, H = 1920, 1080
    PHONE = dict(w=440, h=952, x=1300, y=64, r=44)
    DESK = dict(w=1180, h=940, x=668, y=70, r=28)

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode: print(r.stderr[-3000:]); sys.exit(1)

def wrap(draw, text, font, maxw):
    words, lines, cur = text.split(), [], ''
    for w in words:
        t = (cur + ' ' + w).strip()
        if draw.textlength(t, font=font) <= maxw: cur = t
        else: lines.append(cur); cur = w
    if cur: lines.append(cur)
    return lines

def caption_png(path, head, sub, dev_kind):
    im = Image.new('RGBA', (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    if MODE == 'portrait':
        fh, fs, maxw, y, lh, ls = ImageFont.truetype(F_BOLD, 62), ImageFont.truetype(F_REG, 38), W - 144, 118, 74, 48
    else:
        fh, fs, lh, ls = ImageFont.truetype(F_BOLD, 54), ImageFont.truetype(F_REG, 34), 66, 44
        maxw, y = (1000, 360) if dev_kind == 'phone' else (520, 300)
    for line in wrap(d, head, fh, maxw): d.text((72, y), line, font=fh, fill=FG); y += lh
    if sub:
        y += 8
        for line in wrap(d, sub, fs, maxw): d.text((72, y), line, font=fs, fill=MUTED); y += ls
    im.save(path)

def device_pngs(w, h, r):
    tag = f'{w}x{h}r{r}'
    mask = Image.new('L', (w, h), 0); ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), r, fill=255); mask.save(f'{OUT}/{tag}-mask.png')
    pad = 90; sh = Image.new('RGBA', (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((pad, pad + 18, pad + w, pad + h + 18), r, fill=(0, 0, 0, 150))
    sh = sh.filter(ImageFilter.GaussianBlur(34)); sh.save(f'{OUT}/{tag}-shadow.png')
    bd = Image.new('RGBA', (w, h), (0, 0, 0, 0)); ImageDraw.Draw(bd).rounded_rectangle((0, 0, w - 1, h - 1), r, outline=BORDER + (255,), width=2); bd.save(f'{OUT}/{tag}-border.png')
    return tag, pad

def card_png(path, title, sub, small=None, logo=None):
    im = Image.new('RGBA', (W, H), BG + (255,)); d = ImageDraw.Draw(im)
    y = 700 if MODE == 'portrait' else 300
    if logo:
        lg = Image.open(logo).convert('RGBA'); lg.thumbnail((640, 200)); im.alpha_composite(lg, ((W - lg.width) // 2, y)); y += lg.height + 70
    ft = ImageFont.truetype(F_BOLD, 76)
    fs2 = ImageFont.truetype(F_REG, 44) if title else ImageFont.truetype(F_BOLD, 52)
    fsm = ImageFont.truetype(F_REG, 34)
    maxw = W - 160 if MODE == 'portrait' else W - 400
    for line in wrap(d, title, ft, maxw): d.text(((W - d.textlength(line, font=ft)) / 2, y), line, font=ft, fill=FG); y += 90
    y += 10
    for line in wrap(d, sub, fs2, maxw): d.text(((W - d.textlength(line, font=fs2)) / 2, y), line, font=fs2, fill=MUTED); y += 56
    if small:
        y += 40; d.text(((W - d.textlength(small, font=fsm)) / 2, y), small, font=fsm, fill=TEAL)
    im.convert('RGB').save(path)

def clip_geometry(beat):
    """The device box a clip is framed in, and the ffmpeg filter that trims and scales the source into it."""
    dur, start = beat['dur'], beat.get('start', 0)
    if beat['dev'] == 'phone':
        dev = PHONE
        pre = f"[0:v]trim=start={start}:duration={dur},setpts=PTS-STARTPTS,fps={FPS},scale={dev['w']}:{dev['h']}:flags=lanczos[clip]"
        return dev, pre
    cx, cy, cw, ch = beat['crop']; ex, ey = beat.get('crop_end', (cx, cy))
    if MODE == 'portrait':
        h = int(round(1000 * ch / cw / 2) * 2)
        dev = dict(DESK, h=h, y=max(360, (H - h) // 2 + 60))
    else:
        # Same center as the portrait crop, reshaped to the landscape box; shrink when the capture is too short.
        dev = DESK; ih = DEVICES[beat['dev']]['H']
        nch = min(ih, int(cw * dev['h'] / dev['w'])); ncw = int(nch * dev['w'] / dev['h']) // 2 * 2
        cx, ex = cx + (cw - ncw) // 2, ex + (cw - ncw) // 2; cy = max(0, cy + (ch - nch) // 2); ey = max(0, ey + (ch - nch) // 2)
        cw, ch = ncw, nch
    pre = (f"[0:v]trim=start={start}:duration={dur},setpts=PTS-STARTPTS,fps={FPS},"
           f"crop={cw}:{ch}:x='min(max(0,{cx}+({ex}-{cx})*t/{dur}),iw-{cw})':y='min(max(0,{cy}+({ey}-{cy})*t/{dur}),ih-{ch})',"
           f"scale={dev['w']}:{dev['h']}:flags=lanczos[clip]")
    return dev, pre

def segment_clip(beat, idx):
    dur = beat['dur']; src = f"{CLIPS}/{beat['clip']}.mp4"
    dev, pre = clip_geometry(beat)
    tag, pad = f"{dev['w']}x{dev['h']}r{dev['r']}", 90
    cap = f'{OUT}/cap-{idx}.png'; caption_png(cap, beat['head'], beat.get('sub'), beat['dev'])
    fc = (f"color=c=0x{BG[0]:02x}{BG[1]:02x}{BG[2]:02x}:s={W}x{H}:r={FPS}:d={dur}[bg];"
          f"{pre};[clip][1:v]alphamerge[rounded];"
          f"[bg][2:v]overlay={dev['x'] - pad}:{dev['y'] - pad}[b1];"
          f"[b1][rounded]overlay={dev['x']}:{dev['y']}[b2];"
          f"[b2][3:v]overlay={dev['x']}:{dev['y']}[b3];"
          f"[4:v]format=rgba,fade=t=in:st=0.15:d=0.45:alpha=1[cap];[b3][cap]overlay=0:0:shortest=1,format=yuv420p[v]")
    out = f'{OUT}/seg-{idx:02d}.mp4'
    # Segments are intermediates; the final concat re-encodes, so they get a fast preset at a near-lossless crf.
    run(['ffmpeg', '-y', '-loglevel', 'error', '-i', src, '-loop', '1', '-i', f'{OUT}/{tag}-mask.png', '-loop', '1', '-i', f'{OUT}/{tag}-shadow.png',
         '-loop', '1', '-i', f'{OUT}/{tag}-border.png', '-loop', '1', '-i', cap, '-filter_complex', fc, '-map', '[v]', '-t', str(dur),
         '-c:v', 'libx264', '-crf', '12', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-r', str(FPS), out])
    return out

def segment_card(beat, idx):
    png = f'{OUT}/card-{idx}.png'; card_png(png, beat['head'], beat.get('sub', ''), beat.get('small'), beat.get('logo'))
    dur = beat['dur']; out = f'{OUT}/seg-{idx:02d}.mp4'
    fc = f"[0:v]scale={W*2}:{H*2},zoompan=z='1+0.04*on/({dur*FPS})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(dur*FPS)}:s={W}x{H}:fps={FPS},format=yuv420p[v]"
    run(['ffmpeg', '-y', '-loglevel', 'error', '-loop', '1', '-i', png, '-filter_complex', fc, '-map', '[v]', '-t', str(dur), '-c:v', 'libx264', '-crf', '12', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-r', str(FPS), out])
    return out

def concat(segs, durs, final):
    inputs = [a for s in segs for a in ('-i', s)]
    fc, prev, offset = [], '[0:v]', 0.0
    for i in range(1, len(segs)):
        offset += durs[i - 1] - XF
        tag = '[v]' if i == len(segs) - 1 else f'[x{i}]'
        fc.append(f"{prev}[{i}:v]xfade=transition=fade:duration={XF}:offset={offset:.3f}{tag}"); prev = tag
    run(['ffmpeg', '-y', '-loglevel', 'error', *inputs, '-filter_complex', ';'.join(fc), '-map', '[v]', '-c:v', 'libx264', '-crf', '18', '-preset', 'slow', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-r', str(FPS), final])

beats = SPEC['beats']
for b in beats:
    if 'logo' in b: b['logo'] = os.path.join(D, b['logo'])
chosen = [(i, b) for i, b in enumerate(beats) if not ONLY or str(i) in ONLY]
# Device frames are shared between beats: make each shape once, before the encodes read them in parallel.
for dev in {tuple(sorted(clip_geometry(b)[0].items())) for _, b in chosen if not b.get('card')}:
    d = dict(dev); device_pngs(d['w'], d['h'], d['r'])
# Each segment is its own ffmpeg process, so they encode in parallel; order is kept for the concat.
with ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 1)) as pool:
    segs = list(pool.map(lambda ib: segment_card(ib[1], ib[0]) if ib[1].get('card') else segment_clip(ib[1], ib[0]), chosen))
durs = [b['dur'] for _, b in chosen]
for (i, b), seg in zip(chosen, segs): print('seg', i, b.get('clip', 'card'), os.path.basename(seg))
if not ONLY:
    concat(segs, durs, FINAL); print('done', round(sum(durs) - XF * (len(durs) - 1), 1), 's')
