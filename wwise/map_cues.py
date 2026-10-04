"""Map BL4 music cues (Wwise switch group:value) to the .wem files they play.

Writes data/cue_to_wem.json: {"group:value [+ group:value]": {"wems": [...],
"bytes": N}}. Hash-only names appear as <1234567890> - see switch_names.txt for
where the names come from.
"""
import sys, os, re, json, glob, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config, bank, extract

# Switch-group names the assets never spell out, recovered by hashing candidate
# strings against the group ids in the bank (see CLAUDE.md).
#   mus_gameplay_state - the master group; its subtree reaches every music file
#   arjay_boss         - corroborated by the Mus_Arjay_Boss event name
EXTRA_GROUPS = ["mus_combat_type", "vault_boss", "timekeeper_boss",
                "grasslands_boss", "mountains_boss", "mus_gameplay_state",
                "arjay_boss"]
# UNVERIFIED: hashes to group 2363331616 but reads like a 32-bit collision from
# a 600M-candidate sweep, not a real Wwise group name. Left out deliberately.
SUSPECT_GROUPS = {2363331616: "mus_herbs_pail"}

def switch_names_from_assets(uasset_dir):
    """Pull 'Music.<group>:<value>' out of cooked uasset name tables."""
    names = set()
    for f in glob.glob(os.path.join(uasset_dir, "**", "*.uasset"), recursive=True) + \
             glob.glob(os.path.join(uasset_dir, "**", "*.uexp"), recursive=True):
        blob = open(f, "rb").read()
        for m in re.finditer(rb"[ -~]{8,}", blob):
            for g, v in re.findall(r"Music\.(mus_[A-Za-z0-9_]+):([A-Za-z0-9_]+)",
                                   m.group().decode("ascii")):
                names.add((g, v))
    return names

def hierarchy(bnk_bytes):
    types, parents, track_src = {}, {}, {}
    for t, oid, pl in bank.hirc(bnk_bytes): types[oid] = t
    for t, oid, pl in bank.hirc(bnk_bytes):
        if t in (bank.T_SEGMENT, bank.T_MSWITCH, bank.T_MRANSEQ):
            parents[oid] = bank.parse_music_node(pl)
        elif t == bank.T_TRACK:
            srcs, clips, par = bank.parse_track(pl)
            parents[oid] = par
            track_src[oid] = [s for s, _ in srcs if s]
    children = collections.defaultdict(list)
    for c, p in parents.items(): children[p].append(c)
    return types, children, track_src

def build(bnk_path, names_file=None):
    b = open(bnk_path, "rb").read()
    types, children, track_src = hierarchy(b)

    def wems_under(node, seen=None):
        if seen is None: seen = set()
        if node in seen: return []
        seen.add(node)
        out = list(track_src.get(node, []))
        for c in children.get(node, []): out += wems_under(c, seen)
        return out

    gname, vname = {}, {}
    if names_file and os.path.exists(names_file):
        for line in open(names_file):
            g, _, v = line.strip().partition(":")
            g = g.replace("Music.", "")
            if not g or not v: continue
            gname[bank.fnv(g)] = g
            vname[(bank.fnv(g), bank.fnv(v))] = v
    for g in EXTRA_GROUPS: gname[bank.fnv(g)] = g

    live = json.load(open(config.data("audio_live.json")))
    size = {}
    for path, (ver, pak, usz) in live.items():
        m = re.search(r"Media/(\d+)\.wem$", path)
        if m: size[int(m.group(1))] = usz

    cues = {}
    for t, oid, pl in bank.hirc(b):
        if t != bank.T_MSWITCH: continue
        r = bank.decision_tree_auto(pl)
        if not r: continue
        gs, d, tree = r
        for keys, node in bank.walk_tree(tree, d):
            label = " + ".join(
                f"{gname.get(gi, f'<{gi}>')}:{vname.get((gi, k), f'<{k}>')}"
                for gi, k in zip(gs, keys))
            e = cues.setdefault(label, {"wems": [], "bytes": 0})
            e["wems"] = sorted(set(e["wems"]) | set(wems_under(node)))
            e["bytes"] = sum(size.get(x, 0) for x in e["wems"])
    return cues, track_src, size

if __name__ == "__main__":
    bnk = sys.argv[1] if len(sys.argv) > 1 else config.data("bnk", config.MUSIC_BANK)
    if not os.path.exists(bnk):
        extract.extract([config.WWISE_DIR + config.MUSIC_BANK], config.data("bnk"))
    here = os.path.dirname(os.path.abspath(__file__))
    names = os.path.join(here, "..", "findings", "switch_names.txt")
    cues, track_src, size = build(bnk, names)
    out = config.data("cue_to_wem.json")
    json.dump(cues, open(out, "w"), indent=1)
    allm = set().union(*track_src.values())
    named = sum(1 for k in cues if "<" not in k)
    print(f"{len(cues)} cues ({named} fully named), "
          f"{len(allm)} music .wem, {sum(size.get(w,0) for w in allm)/1048576:.0f} MB")
    print(f"-> {out}")
