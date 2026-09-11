"""Extract .wem / .bnk from the live audio index.

  python extract.py wem  <outdir> <id> [<id> ...]
  python extract.py bank <outdir> [<name.bnk> ...]   (default: all banks)
"""
import sys, os, re, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from pakx import Pak

def _live():
    f = config.data("audio_live.json")
    if not os.path.exists(f):
        import index_audio
        index_audio.build()
    return json.load(open(f))

def extract(paths, outdir):
    """paths: list of full in-pak paths. Returns [(outfile, bytes)]."""
    live = _live()
    os.makedirs(outdir, exist_ok=True)
    cache, out = {}, []
    for path in paths:
        ver, pak, usz = live[path]
        if pak not in cache: cache[pak] = Pak(os.path.join(config.PAKS, pak))
        data = cache[pak].read(path)
        if len(data) != usz:
            raise RuntimeError(f"{path}: got {len(data)} B, index says {usz}")
        dst = os.path.join(outdir, os.path.basename(path))
        open(dst, "wb").write(data)
        out.append((dst, len(data)))
    return out

if __name__ == "__main__":
    mode, outdir = sys.argv[1], sys.argv[2]
    live = _live()
    if mode == "wem":
        want = set(sys.argv[3:])
        paths = [p for p in live
                 if (m := re.search(r"Media/(\d+)\.wem$", p)) and m.group(1) in want]
    else:
        want = set(sys.argv[3:])
        paths = [p for p in live if p.endswith(".bnk")
                 and (not want or os.path.basename(p) in want)]
    for f, n in extract(sorted(paths), outdir):
        print(f"  {os.path.basename(f):>22}  {n:>10} B")
