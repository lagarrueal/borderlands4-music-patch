"""Extract a Wwise File Package (.pck, magic 'AKPK') into loose .bnk / .wem.

Borderlands 2 ships its audio this way (Audio_Banks.pck / Audio_Streaming.pck,
plus one pair per DLC). Layout, all little-endian:

  'AKPK' u32 header_size u32 version
  u32 lang_map_size u32 banks_lut_size u32 streams_lut_size
  [u32 externals_lut_size]   present when header_size accounts for it
  lang map:   u32 count, count * (u32 str_offset, u32 lang_id), UTF-16 names
  banks LUT:  u32 count, count * (u32 id, u32 block_size, u32 size,
                                  u32 start_block, u32 lang_id)
  streams LUT: same shape; the file sits at start_block * block_size

BL2's packages carry the externals field. Assuming the 3-field header reads
the language map as the bank table and extracts garbage - check the sizes add
up to header_size before trusting either layout.

  python akpk.py <out_dir> <file.pck> [...]
"""
import os, struct, sys

def entries(b):
    if b[:4] != b"AKPK":
        raise ValueError("not an AKPK file")
    hdr, ver, lang_sz, bank_sz, stm_sz, ext_sz = struct.unpack_from("<IIIIII", b, 4)
    if hdr == 4 + 16 + lang_sz + bank_sz + stm_sz + ext_sz:
        o = 28                              # version + 4 size fields
    elif hdr == 4 + 12 + lang_sz + bank_sz + stm_sz:
        o = 24                              # version + 3 size fields
    else:
        raise ValueError(f"AKPK header sizes do not add up to {hdr}")
    langs = {}
    n, = struct.unpack_from("<I", b, o)
    for i in range(n):
        soff, lid = struct.unpack_from("<II", b, o + 4 + 8 * i)
        s = b[o + soff:o + lang_sz]
        langs[lid] = s.decode("utf-16-le", "ignore").split("\0")[0]
    o += lang_sz
    for ext, size in (("bnk", bank_sz), ("wem", stm_sz)):
        n, = struct.unpack_from("<I", b, o)
        for i in range(n):
            fid, block, fsz, start, lid = struct.unpack_from("<IIIII", b, o + 4 + 20 * i)
            yield ext, fid, start * block, fsz, langs.get(lid, str(lid))
        o += size

def extract(pck, out_dir):
    b = open(pck, "rb").read()
    os.makedirs(out_dir, exist_ok=True)
    count = {"bnk": 0, "wem": 0}
    for ext, fid, off, size, lang in entries(b):
        if off + size > len(b):
            raise ValueError(f"{pck}: {fid}.{ext} runs past end of file")
        with open(os.path.join(out_dir, f"{fid}.{ext}"), "wb") as f:
            f.write(b[off:off + size])
        count[ext] += 1
    return count

if __name__ == "__main__":
    out = sys.argv[1]
    for p in sys.argv[2:]:
        c = extract(p, out)
        print(f"{os.path.basename(p)}: {c['bnk']} banks, {c['wem']} streamed .wem")
