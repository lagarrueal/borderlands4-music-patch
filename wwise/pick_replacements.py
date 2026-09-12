"""Choose valid replacement .wem for a set of music sources.

Two constraints, both learned the hard way (see ../CLAUDE.md):

  duration - a replacement must be at least as long as the segment declares
             (fSrcDuration), or Wwise streams past end of file and buzzes.
  content  - BL4 ships silent filler .wem that encode to ~2 kbps. Selecting the
             smallest long-enough file picks those every time, and the music
             just disappears. Require a real bitrate.
"""
import sys, os, re, json, bisect, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config, bank

MIN_KBPS = 60          # below ~20 is a sparse pad; ~2 is digital silence

def source_durations(bnk_path):
    """sourceID -> longest declared clip duration in ms."""
    b = open(bnk_path, "rb").read()
    need = {}
    for t, oid, pl in bank.hirc(b):
        if t != bank.T_TRACK: continue
        srcs, clips, par = bank.parse_track(pl)
        for sid, at, dur in clips:
            if sid: need[sid] = max(need.get(sid, 0.0), dur)
    return need

def sizes():
    live = json.load(open(config.data("audio_live.json")))
    out = {}
    for p, (v, pak, usz) in live.items():
        m = re.search(r"Media/(\d+)\.wem$", p)
        if m: out[int(m.group(1))] = usz
    return out

def kbps(w, need, size):
    d = need.get(w, 0) / 1000
    return size[w] * 8 / 1024 / d if d and w in size else 0.0

def choose(targets, pool, need, size):
    """Cheapest pool track at least as long as each target needs."""
    cand = sorted((need[w], size[w], w) for w in pool
                  if w in need and w in size and need[w] > 0
                  and kbps(w, need, size) >= MIN_KBPS)
    durs = [c[0] for c in cand]
    best = [None] * len(cand); cur = None
    for i in range(len(cand) - 1, -1, -1):
        if cur is None or cand[i][1] < cur[1]: cur = cand[i]
        best[i] = cur
    out = {}
    for w in sorted(targets):
        if w not in need or w not in size: continue
        i = bisect.bisect_left(durs, need[w])
        if i < len(cand): out[w] = best[i][2]
    return out

def cue_wems(pattern):
    cues = json.load(open(config.data("cue_to_wem.json")))
    s = set()
    for k, v in cues.items():
        if re.search(pattern, k): s |= set(v["wems"])
    return s

if __name__ == "__main__":
    need = source_durations(config.data("bnk", config.MUSIC_BANK))
    size = sizes()
    tgt_pat = sys.argv[1] if len(sys.argv) > 1 else "mus_combat_type"
    pool_pat = sys.argv[2] if len(sys.argv) > 2 else "ambiance_zone|ambience_zone"
    targets = cue_wems(tgt_pat)
    pool = cue_wems(pool_pat) - targets
    a = choose(targets, pool, need, size)
    total = sum(size[v] for v in a.values())
    print(f"targets matching {tgt_pat!r}: {len(targets)}")
    print(f"pool matching {pool_pat!r}:  {len(pool)}")
    print(f"assigned {len(a)}  ->  {total/1048576:.0f} MB, "
          f"{len(set(a.values()))} distinct tracks")
    silent = [w for w in targets if w in size and kbps(w, need, size) < 5]
    print(f"(targets that are themselves silent filler: {len(silent)})")
