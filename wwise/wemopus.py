"""Write Wwise Opus (.wem) files BL4 can stream.

Container (see ../CLAUDE.md): fmt / hash / seek / data, where the seek chunk is
one u16 per Opus packet giving its byte length, and data is those packets
concatenated with no framing of their own.

  python wemopus.py encode <input audio> <out.wem> [bitrate_kbps]
  python wemopus.py verify <a.wem> [b.wem ...]
"""
import os, struct, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

FMT_TAG, RATE, CHANNELS, FRAME = 0x3041, 48000, 2, 960
CONST_A, CONST_B, CONST_C, CONST_D = 12546, 312, 1, 0

def _ffmpeg():
    p = config.FFMPEG
    if not os.path.exists(p):
        raise SystemExit(f"ffmpeg not found at {p}; set FFMPEG in config.py")
    return p

def ogg_packets(data):
    """Yield packets from an Ogg stream, dropping the two Opus header packets."""
    o, packets, buf = 0, [], b""
    while o < len(data):
        if data[o:o+4] != b"OggS":
            raise ValueError(f"bad Ogg page at {o}")
        nseg = data[o+26]
        segs = data[o+27:o+27+nseg]
        body = o + 27 + nseg
        for s in segs:
            buf += data[body:body+s]; body += s
            if s < 255:
                packets.append(buf); buf = b""
        o = body
    if buf: packets.append(buf)
    return [p for p in packets
            if not (p[:8] == b"OpusHead" or p[:8] == b"OpusTags")]

def encode(src, dst, kbps=96):
    """Any audio ffmpeg reads -> a BL4-compatible Wwise Opus .wem."""
    ff = _ffmpeg()
    tmp = tempfile.mktemp(suffix=".opus")
    try:
        subprocess.run(
            [ff, "-y", "-v", "error", "-i", src,
             "-ac", str(CHANNELS), "-ar", str(RATE),
             "-c:a", "libopus", "-b:a", f"{kbps}k",
             "-frame_duration", "20", "-application", "audio",
             "-vn", "-map_metadata", "-1", tmp],
            check=True)
        packets = ogg_packets(open(tmp, "rb").read())
    finally:
        if os.path.exists(tmp): os.remove(tmp)
    if not packets: raise SystemExit("no Opus packets produced")
    for p in packets:
        if len(p) > 0xFFFF:
            raise SystemExit("packet exceeds the u16 seek entry")
    data = b"".join(packets)
    seek = b"".join(struct.pack("<H", len(p)) for p in packets)
    total = len(packets) * FRAME
    secs = total / RATE
    fmt = struct.pack("<HHIIHH", FMT_TAG, CHANNELS, RATE,
                      int(len(data)/secs), 0, 0) + \
          struct.pack("<HHIII", 18, FRAME, CONST_A, total, len(packets)) + \
          struct.pack("<HBB", CONST_B, CONST_C, CONST_D)
    assert len(fmt) == 36, len(fmt)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt \
         + b"hash" + struct.pack("<I", 16) + b"\0"*16 \
         + b"seek" + struct.pack("<I", len(seek)) + seek \
         + b"data" + struct.pack("<I", len(data)) + data
    out = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body
    open(dst, "wb").write(out)
    return {"packets": len(packets), "samples": total, "seconds": secs,
            "bytes": len(out)}

def probe(wem):
    """vgmstream's view of a .wem - the independent check that it is valid."""
    r = subprocess.run([config.VGMSTREAM, "-m", wem],
                       capture_output=True, text=True)
    if r.returncode != 0: return None
    out = {}
    for line in r.stdout.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out

if __name__ == "__main__":
    if sys.argv[1] == "encode":
        kb = int(sys.argv[4]) if len(sys.argv) > 4 else 96
        info = encode(sys.argv[2], sys.argv[3], kb)
        print(f"{info['packets']} packets, {info['samples']} samples "
              f"({info['seconds']:.2f}s), {info['bytes']:,} B")
        p = probe(sys.argv[3])
        print("vgmstream:", p.get("encoding"), p.get("play duration") if p else "REJECTED")
    else:
        for f in sys.argv[2:]:
            p = probe(f)
            print(f"{os.path.basename(f)}: "
                  f"{p.get('play duration') if p else 'REJECTED'}")
