"""Regenerate real_format.npz: ChromaPrint ints of synthesized audio, clean and degraded.

Needs fpcalc and ffmpeg on PATH. Not run by the tests. Usage: python make_real_format.py
"""
import json
import os
import subprocess
import tempfile
import wave

import numpy as np

SR = 11025
SECONDS = 180
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'real_format.npz')


def speech(rng, secs):
    """Voiced syllables with formants and short pauses."""
    out, t = [], 0.0
    while t < secs:
        d = rng.uniform(0.08, 0.35)
        n = int(d * SR)
        tt = np.arange(n) / SR
        if rng.random() < 0.12:
            out.append(rng.normal(0, 0.003, n))
            t += d
            continue
        f0 = rng.uniform(90, 230) * (1 + 0.1 * np.sin(2 * np.pi * rng.uniform(1, 5) * tt))
        ph = 2 * np.pi * np.cumsum(f0) / SR
        voiced = sum(np.sin(k * ph) / k for k in range(1, 18))
        formants = rng.uniform([300, 900, 2200], [900, 2200, 3200])
        spec = np.fft.rfft(voiced + (rng.normal(0, 0.6, n) if rng.random() < 0.3 else 0))
        fr = np.fft.rfftfreq(n, 1 / SR)
        gain = sum(np.exp(-((fr - f) / 120) ** 2) for f in formants) + 0.05
        out.append(np.fft.irfft(spec * gain, n) * np.hanning(n) ** 0.5 * rng.uniform(0.3, 1))
        t += d
    x = np.concatenate(out)[:int(secs * SR)]
    return np.pad(x, (0, int(secs * SR) - len(x)))


def music(rng, secs):
    """Decaying notes from a random seven-note scale with percussive noise."""
    out = np.zeros(int(secs * SR) + SR)
    scale = rng.choice(12, 7, replace=False)
    t = 0.0
    while t < secs:
        d = rng.choice([0.125, 0.25, 0.25, 0.5, 0.75]) * rng.uniform(0.9, 1.1)
        n = int(d * SR)
        tt = np.arange(n) / SR
        a = int(t * SR)
        env = np.exp(-tt * rng.uniform(2, 8))
        for _ in range(rng.integers(1, 4)):
            f = 440 * 2 ** ((48 + 12 * rng.integers(0, 3) + rng.choice(scale) - 69) / 12)
            out[a:a + n] += env * sum(np.sin(2 * np.pi * f * k * tt) / k ** 1.5
                                      for k in range(1, 6)) * rng.uniform(0.3, 1)
        if rng.random() < 0.4:
            m = min(n, 800)
            out[a:a + m] += rng.normal(0, 0.5, m) * np.exp(-np.arange(m) / 150)
        t += d
    return out[:int(secs * SR)]


def write_wav(path, x):
    x = np.clip(x / (np.abs(x).max() + 1e-9) * 0.8, -1, 1)
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def fingerprint(path):
    out = subprocess.run(['fpcalc', '-raw', '-json', '-length', '0', path],
                         check=True, capture_output=True).stdout
    return (np.array(json.loads(out)['fingerprint'], dtype=np.int64) & 0xFFFFFFFF).astype(np.uint32)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        a, b = os.path.join(tmp, 'a.wav'), os.path.join(tmp, 'b.wav')
        write_wav(a, speech(np.random.default_rng(1), SECONDS)
                  + 0.35 * music(np.random.default_rng(2), SECONDS))
        write_wav(b, speech(np.random.default_rng(3), SECONDS)
                  + 0.35 * music(np.random.default_rng(4), SECONDS))
        # Telephone band, 24 kbps MP3: a heavy but realistic degradation
        degraded = os.path.join(tmp, 'a_tel.mp3')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', a, '-af', 'highpass=300,lowpass=3400',
                        '-b:a', '24k', degraded], check=True)
        np.savez_compressed(OUT, clean_a=fingerprint(a), degraded_a=fingerprint(degraded),
                            clean_b=fingerprint(b))


if __name__ == '__main__':
    main()
