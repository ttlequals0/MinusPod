"""OCR every second of a finished video for the patterns in targets.json.

Usage: python3 ocrcheck.py out/*.mp4        (pip install rapidocr_onnxruntime)
Catches text the DOM checks cannot see, such as chart labels drawn on a canvas.
Exits 1 when anything matches; the project's own GitHub URL on the end cards is expected and skipped.
"""
import glob, json, os, re, subprocess, sys, tempfile
from rapidocr_onnxruntime import RapidOCR

D = os.path.dirname(os.path.abspath(__file__))
T = json.load(open(os.environ.get('TARGETS', f'{D}/targets.json')))
PAT = re.compile('|'.join(T['leakPatterns'] + T.get('excludeShows', [])), re.I)
ocr = RapidOCR(); hits = 0
for video in sys.argv[1:]:
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', video, '-vf', 'fps=1', f'{d}/f%04d.png'], check=True)
        frames = sorted(glob.glob(f'{d}/*.png'))
        for f in frames:
            result, _ = ocr(f)
            for _box, text, _conf in result or []:
                if PAT.search(text) and 'github.com' not in text.lower():
                    print('HIT', os.path.basename(video), os.path.basename(f), repr(text)); hits += 1
        print(os.path.basename(video), len(frames), 'frames checked')
print('hits', hits); sys.exit(1 if hits else 0)
