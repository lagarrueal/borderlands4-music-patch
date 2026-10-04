"""BL4 legacy-pak reader: full index (incl. deletion sentinels) + Oodle extraction.

repak panics on paks that contain deletion records (entry index == INT32_MIN),
and three of BL4's pakchunk2 paks do - including the one holding the newest
copy of the master music bank. So we parse and extract ourselves.
"""
import ctypes, os, struct

_OODLE = None
def _oodle():
    global _OODLE
    if _OODLE is None:
        from config import OODLE
        dll = ctypes.CDLL(OODLE)
        fn = dll.OodleLZ_Decompress
        fn.restype = ctypes.c_longlong
        fn.argtypes = [ctypes.c_void_p, ctypes.c_longlong, ctypes.c_void_p,
                       ctypes.c_longlong] + [ctypes.c_longlong]*3 + \
                      [ctypes.c_void_p, ctypes.c_longlong, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_longlong,
                       ctypes.c_longlong]
        _OODLE = fn
    return _OODLE

def oodle_decompress(src, out_size):
    fn = _oodle()
    dst = ctypes.create_string_buffer(out_size)
    n = fn(src, len(src), dst, out_size, 1, 0, 0, None, 0, None, None, None, 0, 3)
    if n != out_size:
        raise RuntimeError(f"oodle returned {n}, expected {out_size}")
    return dst.raw[:out_size]

DELETED = -2147483648

def rstr(b, o):
    n, = struct.unpack_from("<i", b, o); o += 4
    if n < 0: s = b[o:o-2*n-2].decode("utf-16-le","ignore"); o += -2*n
    else:     s = b[o:o+n-1].decode("utf-8","ignore"); o += n
    return s, o

class Pak:
    def __init__(self, path):
        self.path = path
        f = open(path, "rb"); self.f = f
        f.seek(0, 2); size = f.tell()
        f.seek(max(0, size-1024)); tail = f.read()
        pos = tail.rfind(b"\xe1\x12\x6f\x5a")
        if pos < 0: raise RuntimeError("no pak footer")
        o = pos + 4
        self.version, = struct.unpack_from("<I", tail, o); o += 4
        idx_off, = struct.unpack_from("<Q", tail, o); o += 8
        idx_size, = struct.unpack_from("<Q", tail, o); o += 8
        f.seek(idx_off); idx = f.read(idx_size); o = 0
        self.mount, o = rstr(idx, o)
        o += 4 + 8
        hp, = struct.unpack_from("<i", idx, o); o += 4
        if hp: o += 36
        hf, = struct.unpack_from("<i", idx, o); o += 4
        fdi_off, fdi_size = struct.unpack_from("<QQ", idx, o); o += 16 + 20
        esz, = struct.unpack_from("<i", idx, o); o += 4
        self.enc = idx[o:o+esz]
        f.seek(fdi_off); fdi = f.read(fdi_size); p = 0
        nd, = struct.unpack_from("<i", fdi, p); p += 4
        self.entries = {}      # path -> encoded offset
        self.deleted = set()
        for _ in range(nd):
            d, p = rstr(fdi, p)
            nf, = struct.unpack_from("<i", fdi, p); p += 4
            for _ in range(nf):
                fn, p = rstr(fdi, p)
                e, = struct.unpack_from("<i", fdi, p); p += 4
                full = self.mount + d.lstrip("/") + fn
                if e == DELETED: self.deleted.add(full)
                else:            self.entries[full] = e

    def meta(self, path):
        """(data_offset, compressed_size, uncompressed_size, method) from encoded index."""
        e = self.entries[path]
        v, = struct.unpack_from("<I", self.enc, e); q = e + 4
        method = (v >> 23) & 0x3F
        if (v >> 31) & 1: off, = struct.unpack_from("<I", self.enc, q); q += 4
        else:             off, = struct.unpack_from("<Q", self.enc, q); q += 8
        if (v >> 30) & 1: usz, = struct.unpack_from("<I", self.enc, q); q += 4
        else:             usz, = struct.unpack_from("<Q", self.enc, q); q += 8
        if method:
            csz, = struct.unpack_from("<I" if (v >> 29) & 1 else "<Q", self.enc, q)
        else:
            csz = usz
        return off, csz, usz, method

    def read(self, path):
        off, csz, usz, method = self.meta(path)
        f = self.f
        f.seek(off)
        head = f.read(64 + 4)
        # FPakEntry re-serialised at the data offset
        p = 8 + 8 + 8 + 4 + 20          # offset, size, usize, methodIdx, sha1
        if method:
            f.seek(off + p)
            nblk, = struct.unpack("<i", f.read(4))
            blocks = []
            for _ in range(nblk):
                s, e = struct.unpack("<qq", f.read(16))
                blocks.append((s, e))
            p += 4 + nblk * 16
            f.seek(off + p); p += 1 + 4
            flags_bs = f.read(5)
            data_start = off + p
            out = bytearray()
            # block offsets are relative to the entry header start in pak v11
            for s, e in blocks:
                f.seek(off + s)
                chunk = f.read(e - s)
                remain = usz - len(out)
                out += oodle_decompress(chunk, min(remain, 65536)
                                        if remain > 65536 else remain)
            return bytes(out)
        else:
            f.seek(off + p + 1 + 4)
            return f.read(usz)
