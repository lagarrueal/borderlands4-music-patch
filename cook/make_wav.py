"""Generate the test tone used to probe BL4's audio path.

4 seconds, 440Hz with a 3Hz warble and short fades so it does not click.
Deliberately unlike anything in Borderlands, so "did I hear it?" is not a
judgement call - a lesson from the music-remap test, where cycling between
similar ambient cues produced an unusable "I think I heard a change".

    python cook/make_wav.py [out.wav]
"""

from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

SAMPLE_RATE = 44100
SECONDS = 4.0
FREQ_HZ = 440.0
WARBLE_HZ = 3.0
AMPLITUDE = 18000
FADE = 0.05


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "ProbeTone.wav")
    frames = bytearray()
    total = int(SAMPLE_RATE * SECONDS)
    for i in range(total):
        t = i / SAMPLE_RATE
        amp = AMPLITUDE
        if t < FADE:
            amp *= t / FADE
        if t > SECONDS - FADE:
            amp *= (SECONDS - t) / FADE
        warble = 0.6 + 0.4 * math.sin(2 * math.pi * WARBLE_HZ * t)
        v = int(amp * math.sin(2 * math.pi * FREQ_HZ * t) * warble)
        frames += struct.pack("<h", max(-32768, min(32767, v)))

    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(frames))

    # 44100 * 4 * 2 = 352,800 bytes of PCM. The cooked .ubulk should match this
    # exactly - that is how you confirm the asset really cooked as PCM and not
    # as a codec BL4 may not carry a decoder for.
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
