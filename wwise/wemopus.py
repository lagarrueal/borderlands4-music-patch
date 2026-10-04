"""Write Wwise Opus (.wem) files BL4 can stream.

Container (see ../CLAUDE.md): fmt / hash / seek / data, where the seek chunk is
one u16 per Opus packet giving its byte length, and data is those packets
concatenated with no framing of their own. A bank source that streams a file
must declare its prefetch_size().

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
    """Audio packets of an Ogg Opus stream (header packets dropped), plus the
    stream's pre-skip and the granule position of its last page."""
    o, packets, buf, granule = 0, [], b"", 0
    while o < len(data):
        if data[o:o+4] != b"OggS":
            raise ValueError(f"bad Ogg page at {o}")
        granule = struct.unpack_from("<q", data, o + 6)[0]
        nseg = data[o+26]
        segs = data[o+27:o+27+nseg]
        body = o + 27 + nseg
        for s in segs:
            buf += data[body:body+s]; body += s
            if s < 255:
                packets.append(buf); buf = b""
        o = body
    if buf: packets.append(buf)
    head = next((p for p in packets if p[:8] == b"OpusHead"), None)
    preskip = struct.unpack_from("<H", head, 10)[0] if head else CONST_B
    audio = [p for p in packets
             if not (p[:8] == b"OpusHead" or p[:8] == b"OpusTags")]
    return audio, preskip, granule

def encode(src, dst, kbps=96, af=None):
    """Any audio ffmpeg reads -> a BL4-compatible Wwise Opus .wem.

    totalSamples is the exact decoded length (last granule minus pre-skip), as
    in BL4's own files. It becomes the clip length when a music segment loops
    the file, so a frame-padded count would leave a gap in the loop.
    """
    ff = _ffmpeg()
    tmp = tempfile.mktemp(suffix=".opus")
    try:
        subprocess.run(
            [ff, "-y", "-v", "error", "-i", src] + (["-af", af] if af else []) +
            ["-ac", str(CHANNELS), "-ar", str(RATE),
             "-c:a", "libopus", "-b:a", f"{kbps}k",
             "-frame_duration", "20", "-application", "audio",
             "-vn", "-map_metadata", "-1", tmp],
            check=True)
        packets, preskip, granule = ogg_packets(open(tmp, "rb").read())
    finally:
        if os.path.exists(tmp): os.remove(tmp)
    if not packets: raise SystemExit("no Opus packets produced")
    for p in packets:
        if len(p) > 0xFFFF:
            raise SystemExit("packet exceeds the u16 seek entry")
    data = b"".join(packets)
    seek = b"".join(struct.pack("<H", len(p)) for p in packets)
    total = granule - preskip
    if not 0 < total <= len(packets) * FRAME:
        total = len(packets) * FRAME - preskip
    secs = total / RATE
    fmt = struct.pack("<HHIIHH", FMT_TAG, CHANNELS, RATE,
                      int(len(data)/secs), 0, 0) + \
          struct.pack("<HHIII", 18, FRAME, CONST_A, total, len(packets)) + \
          struct.pack("<HBB", preskip, CONST_C, CONST_D)
    assert len(fmt) == 36, len(fmt)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt \
         + b"hash" + struct.pack("<I", 16) + b"\0"*16 \
         + b"seek" + struct.pack("<I", len(seek)) + seek \
         + b"data" + struct.pack("<I", len(data)) + data
    out = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body
    open(dst, "wb").write(out)
    return {"packets": len(packets), "samples": total, "seconds": secs,
            "bytes": len(out)}

PREFETCH_PACKETS = 6

def prefetch_size(wem):
    """The in-memory size a BL4 bank declares for a streamed .wem: the header
    (everything before the audio) plus the first 6 Opus packets. All 2,839
    music sources of the shipped maingame bank follow this exactly.

    The game preloads that many bytes and parses the header from them, so a
    bank source pointed at a new file must declare the new file's size. A
    longer track has a bigger seek table, and a stale smaller size leaves
    the track silent in game while every offline render still plays it.
    """
    with open(wem, "rb") as fh:
        fh.seek(12)
        seek = None
        while True:
            head = fh.read(8)
            if len(head) < 8:
                raise ValueError(f"{wem}: no data chunk")
            tag, size = head[:4], struct.unpack("<I", head[4:])[0]
            if tag == b"data":
                if seek is None:
                    raise ValueError(f"{wem}: no seek chunk before the data")
                return fh.tell() + sum(seek)
            start = fh.tell()
            if tag == b"seek":
                seek = struct.unpack(f"<{PREFETCH_PACKETS}H", fh.read(2 * PREFETCH_PACKETS))
            fh.seek(start + size + (size & 1))

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
