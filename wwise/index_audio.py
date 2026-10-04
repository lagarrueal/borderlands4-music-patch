"""Resolve the live (newest-wins) copy of every WwiseAudio file -> data/audio_live.json.

repak cannot read three of BL4's pakchunk2 paks (they contain patch deletion
records), and one of them holds the newest master music bank - so this walks
the pak indices directly via pakx.
"""
import sys, os, re, json, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from pakx import Pak

def build():
    best = {}
    for pk in sorted(glob.glob(os.path.join(config.PAKS, config.AUDIO_PAK_GLOB))):
        ver = int(re.search(r"_(\d+)_P\.pak$", pk).group(1))
        p = Pak(pk)
        base = os.path.basename(pk)
        for path in p.entries:
            _, _, usz, _ = p.meta(path)
            if path not in best or ver > best[path][0]:
                best[path] = (ver, base, usz, False)
        for path in p.deleted:
            if path not in best or ver > best[path][0]:
                best[path] = (ver, base, 0, True)
    live = {k: v[:3] for k, v in best.items() if not v[3]}
    dead = [k for k, v in best.items() if v[3]]
    out = config.data("audio_live.json")
    json.dump(live, open(out, "w"))
    return live, dead, out

if __name__ == "__main__":
    live, dead, out = build()
    wem = sum(1 for k in live if re.search(r"Media/\d+\.wem$", k))
    bnk = sum(1 for k in live if k.endswith(".bnk"))
    print(f"{len(live)} live files ({wem} media .wem, {bnk} banks), "
          f"{len(dead)} deleted by patches")
    print(f"-> {out}")
