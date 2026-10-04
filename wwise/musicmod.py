"""Put other games' music into BL4's combat and zone music slots.

  python musicmod.py <spec.json> [--install] [--dry-run]

The spec names BL4 slots and the listening-library tracks that replace them
(ids as listen/tracks.js and the picker's "Copy for Claude" print them):

  {"name": "BLMusicMix_9700_P",
   "combat": [{"container": 513039619, "slot": "Fadefields", "page": "grasslands",
               "basic": "bl2/PandoraPark_P/Combat", "critical": "bl2/PandoraPark_P/Boss_1"}],
   "zones":  [{"container": 943367025, "slot": "Fadefields exploration", "page": "gr_",
               "track": "bl3/Mus_Wetlands_Play/s01"}]}

Everything is patched in place - no byte of the bank moves - and is checked
again by wwiser before anything is packed:

  combat  per combat type: the random intros are pointed at the loop segment,
          the loop is stretched to the new track, one stem plays the track and
          the rest play silence. The 'falling' state is pointed at the same
          playlist, so the track carries on until combat music fades out
          instead of cutting to BL4's own outro.
  zones   every zone of the container is pointed at one playlist, which plays
          the new track the same way. Moving between zones keeps it playing.

New audio is only written to media ids that nothing reachable uses any more.
Every bank source that streams a new file then declares that file's own
preload size (wemopus.prefetch_size): the game preloads exactly that many
bytes and needs the whole header in them, or the track is silent in game.
"""
import argparse, json, os, re, shutil, struct, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bank as rawbank, bnktree, config, listen, musicncs, wemopus

HERE = os.path.dirname(os.path.abspath(__file__))
BANK_ID = 1731745708
SRC_BANK = config.data("bl4", f"{BANK_ID}.bnk")
ORIG_WEM = config.data("bl4", "txtp", "wem")
WORK = config.data("bl4mod")
ENTRY_CUE, EXIT_CUE = 43573010, 1539036744
KBPS = 128
RATE = 48000
OPUS_PLUGIN = 0x00140001
fnv = rawbank.fnv


# --------------------------------------------------------------------------
# reading the bank through wwiser

def own(node):
    """Fields directly on an object node: name -> (offset, value)."""
    return {c.get_attr("name"): (c.get_attr("offset"), c.get_attr("value"))
            for c in node.get_children() or () if c.get_nodename() == "field"}

def tree_leaves(b, cid):
    """Decision-tree leaves of a music switch container: [(keys, (offset, nodeId))]."""
    out = []
    def rec(node, path):
        for c in node.get_children() or ():
            if c.get_nodename() == "object":
                f = own(c)
                if "key" in f:
                    p = path + [f["key"][1]]
                    if "audioNodeId" in f:
                        out.append((tuple(p[1:]), f["audioNodeId"]))
                    rec(c, p)
                    continue
            rec(c, path)
    rec(b.obj[cid], [])
    depth = b.field1(b.obj[cid], "uTreeDepth")[1]
    bad = [k for k, _ in out if len(k) != depth]
    if bad:
        raise RuntimeError(f"tree of {cid}: leaf paths {bad[:3]} do not match depth {depth}")
    return out

def tree_leaf_fields(b, cid):
    """Like tree_leaves, but each leaf as {'keys', 'key', 'node'} with the key's
    own (offset, value) too, for leaves whose key gets rewritten."""
    out = []
    def rec(node, path):
        for c in node.get_children() or ():
            if c.get_nodename() == "object":
                f = own(c)
                if "key" in f:
                    p = path + [f["key"][1]]
                    if "audioNodeId" in f:
                        out.append({"keys": tuple(p[1:]), "key": f["key"], "node": f["audioNodeId"]})
                    rec(c, p)
                    continue
            rec(c, path)
    rec(b.obj[cid], [])
    return out

def switch_names():
    """FNV id -> lowercase name, from the game's own Wwise name tables."""
    names = {}
    for line in open(config.data("bl4", "wwnames.txt"), encoding="utf-8"):
        n = line.strip()
        if n:
            names.setdefault(fnv(n), n.lower())
    return names

def automations(b, tid):
    """Clip automation curves of a track: [(eAutoType, [point field dicts])]."""
    out = []
    for a in b.objects(b.obj[tid], "AkClipAutomation"):
        out.append((own(a)["eAutoType"][1], [own(p) for p in b.objects(a, "AkRTPCGraphPoint")]))
    return out

# unity value of a clip automation curve, per eAutoType
FLAT = {0: 0.0, 1: 0.0, 2: 0.0, 3: 1.0, 4: 1.0}   # volume dB, LPF, HPF, fade-in, fade-out

# neutral value of an RTPC curve, per ParamID: Volume, LPF, HPF, MakeUpGain.
# 0 means "no change" for each, including dB-scaled volume curves, which BL4
# stores normalized to -1..1 with -1 = silent (all 1,568 of them in the bank)
RTPC_NEUTRAL = {0: 0.0, 3: 0.0, 4: 0.0, 7: 0.0}

def rtpc_curves(b, nid):
    """RTPC curves set on an object itself: [(ParamID, RTPC id, [point field dicts])]."""
    rtpcs = [n for n in bnktree.walk(b.obj[nid])
             if n.get_nodename() == "object" and n.get_attr("name") == "RTPC"]
    return [(own(r)["ParamID"][1], own(r)["RTPCID"][1], [own(p) for p in b.objects(r, "AkRTPCGraphPoint")])
            for r in rtpcs]

def output_bus(b, nid):
    """The bus a node plays on: its own OverrideBusId, else its nearest ancestor's."""
    while nid:
        bus = b.field1(b.obj[nid], "OverrideBusId")[1]
        if bus:
            return bus
        nid = b.parent_id(nid)
    return 0

def playlist_leaf_items(b, pid):
    """[(offset of SegmentID, segment id)] for every leaf item of a playlist."""
    out = []
    for o in b.objects(b.obj[pid], "AkMusicRanSeqPlaylistItem"):
        f = own(o)
        if f["SegmentID"][1]:
            out.append(f["SegmentID"])
    return out

VOLUME_PROPS = (0, 6)        # AkPropID Volume, MakeUpGain (dB)

def track(b, tid):
    t = b.obj[tid]
    srcs = [own(m)["sourceID"] for m in b.objects(t, "AkMediaInformation")]
    clips = [own(c) for c in b.objects(t, "AkTrackSrcInfo")]
    props = list(zip((v for _, v in b.fields(t, "pID")), (v for _, v in b.fields(t, "pValue"))))
    return {"id": tid, "srcs": srcs, "clips": clips,
            "autos": b.field1(t, "numClipAutomationItem")[1],
            "curves": b.field1(t, "uNumCurves")[1],
            "states": b.field1(t, "ulNumStateGroups")[1],
            "volume": sum(v for k, v in props if k in VOLUME_PROPS),
            "type": b.field1(t, "eTrackType")[1],
            "subtracks": b.field1(t, "numSubTrack")[1]}

def host_score(t, flatten=False):
    """Lower is better; None if the track cannot carry a whole new track.
    flatten=True accepts clip automation, which apply() then sets to unity."""
    if t["type"] != 0 or t["subtracks"] != 1 or len(t["srcs"]) != 1 or len(t["clips"]) != 1:
        return None
    if (t["autos"] and not flatten) or t["curves"] or t["states"]:
        return None           # clip fades / RTPC / state volumes would shape the new music
    # the loudest stem: BL4 balances stems with offsets down to -7 dB, and a quiet
    # host forces the new music through a limiter to reach the original level
    return -t["volume"]

def segment(b, sid):
    s = b.obj[sid]
    marks = [own(m) for m in b.objects(s, "AkMusicMarkerWwise")]
    return {"id": sid, "dur": b.field1(s, "fDuration"), "marks": marks,
            "tracks": [track(b, t) for t in b.children_ids(sid)]}

def source_fields(b):
    """Every music track source: plugin, and the (offset, value) of its media id
    and of its declared preload size (uInMemoryMediaSize)."""
    out = []
    for tid, kind in b.kind.items():
        if kind != "CAkMusicTrack":
            continue
        for bsd in b.objects(b.obj[tid], "AkBankSourceData"):
            mi = own(b.objects(bsd, "AkMediaInformation")[0])
            out.append({"track": tid, "plugin": own(bsd)["ulPluginID"][1],
                        "source": mi["sourceID"], "inmem": mi["uInMemoryMediaSize"]})
    return out

def check_prefetch(data, srcs, overrides):
    """BL4's own rule over a whole bank: every Opus music source declares exactly
    its file's wemopus.prefetch_size(). Returns (sources checked, violations)."""
    sizes, bad, n = {}, [], 0
    for s in srcs:
        if s["plugin"] != OPUS_PLUGIN:
            continue
        mid = struct.unpack_from("<I", data, s["source"][0])[0]
        path = overrides.get(mid) or os.path.join(ORIG_WEM, f"{mid}.wem")
        if not os.path.exists(path):
            continue
        if path not in sizes:
            sizes[path] = wemopus.prefetch_size(path)
        n += 1
        got = struct.unpack_from("<I", data, s["inmem"][0])[0]
        if got != sizes[path]:
            bad.append({"track": s["track"], "media": mid, "declared": got, "file": sizes[path]})
    return n, bad

def prefetch_estimate(samples, kbps):
    """wemopus.prefetch_size() of a file not encoded yet, rounded up: the header
    holds a u16 per 20 ms packet, then 6 packets of audio (VBR, so 2.5x the mean)."""
    packets = -(-(samples + 312) // 960) + 2
    return 96 + 2 * packets + 6 * int(kbps * 1000 / 8 * 0.02 * 2.5)

def assign_media(wanted, pool, srcs):
    """Pick a free media id per new file, largest first in `wanted` order.

    The bank's declared preload sizes are patched to fit the new files anyway;
    on top of that, each file gets an id whose ORIGINAL declared size already
    holds its header where one exists (the smallest that does), so the music
    plays even if the game took the size from anywhere but the bank.
    wanted: [(key, estimated preload)]; returns {key: (media id, original size)}."""
    cap = {}
    for s in srcs:
        mid = s["source"][1]
        cap[mid] = min(cap.get(mid, 1 << 31), s["inmem"][1])
    left, out, later = list(pool), {}, []
    for key, need in wanted:
        fits = [m for m in left if cap.get(m, 0) >= need]
        if not fits:
            later.append(key)
            continue
        mid = min(fits, key=lambda m: cap[m])
        left.remove(mid)
        out[key] = (mid, cap[mid])
    for key in later:          # no original size holds these: leave the big ids to the rest
        mid = min(left, key=lambda m: cap.get(m, 0))
        left.remove(mid)
        out[key] = (mid, cap.get(mid, 0))
    return out


# --------------------------------------------------------------------------
# planning

def plan(spec, b, usage):
    """Decide every patch and media id without touching anything."""
    slots, freed_tracks = [], set()

    def subtree_tracks(node_id):
        out, stack = set(), [node_id]
        while stack:
            n = stack.pop()
            if b.kind.get(n) == "CAkMusicTrack":
                out.add(n)
            elif b.kind.get(n, "").startswith("CAkMusic"):
                stack += b.children_ids(n)
        return out

    def loop_host(pid, what, flatten=False):
        items = playlist_leaf_items(b, pid)
        segs = {sid: segment(b, sid) for _, sid in items}
        loop = max(segs.values(), key=lambda s: s["dur"][1])
        cands = sorted((host_score(t, flatten), i) for i, t in enumerate(loop["tracks"])
                       if host_score(t, flatten) is not None)
        if not cands:
            raise RuntimeError(f"{what}: no track in segment {loop['id']} can carry the new music")
        host = loop["tracks"][cands[0][1]]
        unreached = set(segs) - {loop["id"]}
        return items, loop, host, unreached

    for c in spec.get("combat", []):
        cid = c["container"]
        leaves = {tuple(k): v for k, v in tree_leaves(b, cid)}
        for ctype in ("basic", "critical"):
            if ctype not in c:
                continue
            p_leaf = leaves.get((fnv(ctype), fnv("combat")))
            f_leaf = leaves.get((fnv(ctype), fnv("falling")))
            if not p_leaf:
                raise RuntimeError(f"{c['slot']}: no {ctype}/combat leaf in {cid}")
            pid = p_leaf[1]
            items, loop, host, unreached = loop_host(pid, f"{c['slot']} {ctype}")
            slot = {"kind": "combat", "slot": c["slot"], "ctype": ctype, "container": cid,
                    "page": c["page"], "source": c[ctype], "playlist": pid,
                    "items": items, "loop": loop, "host": host,
                    "repoint_leaves": [(f_leaf, pid, f"{ctype}/falling -> combat playlist")] if f_leaf else []}
            slots.append(slot)
            for sid in unreached:
                freed_tracks |= subtree_tracks(sid)
            freed_tracks |= {t["id"] for t in loop["tracks"]}
            if f_leaf and f_leaf[1] != pid:
                still = [k for k, v in leaves.items() if v[1] == f_leaf[1] and k != (fnv(ctype), fnv("falling"))]
                if not still:
                    freed_tracks |= subtree_tracks(f_leaf[1])

    for a in spec.get("biome_ambient", []):
        # The biome container already plays everywhere its biome is painted, in
        # every state, but only has leaves for combat, falling and (under the
        # wildcard combat type) rising. Re-keying that rising leaf to "any state"
        # makes ambient and rising play its playlist, and that playlist - the
        # pre-combat riser - becomes the exploration track.
        cid = a["container"]
        rising = [l for l in tree_leaf_fields(b, cid) if l["keys"][-1] == fnv("rising")]
        if len(rising) != 1 or rising[0]["keys"][0] != 0:
            raise RuntimeError(f"{a['slot']}: expected one wildcard rising leaf in {cid}")
        leaf = rising[0]
        pid = leaf["node"][1]
        items, loop, host, unreached = loop_host(pid, a["slot"], flatten=True)
        # The riser fades in with the threat: Music_Threat curves on the playlist
        # itself silence it at zero threat and filter it below combat, so they
        # are made neutral. It also plays on its own bus (-2 dB, panned for the
        # build-up); the exploration track goes where BL4's exploration music
        # plays, the bus of the zone container named in level_from.
        curves = rtpc_curves(b, pid)
        declared = b.field1(b.obj[pid], "uNumCurves")[1]
        if declared != len(curves):
            raise RuntimeError(f"{a['slot']}: riser {pid} declares {declared} RTPC curves, found {len(curves)}")
        odd = sorted({prm for prm, _, _ in curves if prm not in RTPC_NEUTRAL})
        if odd:
            raise RuntimeError(f"{a['slot']}: riser {pid} has RTPC curves on params {odd}, no neutral value known")
        slots.append({"kind": "biome", "slot": a["slot"], "container": cid, "page": a["page"],
                      "source": a["track"], "playlist": pid, "items": items, "loop": loop, "host": host,
                      "rekey": (leaf["key"], 0, "rising leaf -> any gameplay state"),
                      "repoint_leaves": [], "flatten": automations(b, host["id"]), "curves": curves,
                      "bus": (b.field1(b.obj[pid], "OverrideBusId"), output_bus(b, a["level_from"]))
                      if a.get("level_from") else None,
                      "zone_playlists": sorted({v for _, (_, v) in tree_leaves(b, a["level_from"])})
                      if a.get("level_from") else None})
        freed_tracks |= {t["id"] for t in loop["tracks"]}
        for sid in unreached:
            freed_tracks |= subtree_tracks(sid)

    names = switch_names()
    for z in spec.get("zones", []):
        cid = z["container"]
        leaves = tree_leaves(b, cid)
        keep = [k.lower() for k in z.get("keep", [])]
        kept = lambda key: not keep or any(k in names.get(key[0], "") for k in keep)
        best = None
        for child in sorted({v[1] for _, v in leaves}):
            if b.kind.get(child) != "CAkMusicRanSeqCntr":
                continue
            try:
                items, loop, host, unreached = loop_host(child, z["slot"])
            except RuntimeError:
                continue
            key = (len(items), host_score(host), len(loop["tracks"]))
            if best is None or key < best[0]:
                best = (key, child, items, loop, host, unreached)
        if best is None:
            raise RuntimeError(f"{z['slot']}: no zone playlist can host the new music")
        _, pid, items, loop, host, unreached = best
        # zones outside `keep` play nothing: the biome container covers them,
        # and two copies of one track started at different times would clash
        slot = {"kind": "zone", "slot": z["slot"], "container": cid, "page": z["page"],
                "source": z["track"], "playlist": pid, "items": items, "loop": loop, "host": host,
                "repoint_leaves": [((off, v), pid if kept(k) else 0,
                                    f"zone {names.get(k[0], k[0])} -> {'host' if kept(k) else 'nothing'}")
                                   for k, (off, v) in leaves if v != (pid if kept(k) else 0)],
                "zones": len(leaves),
                "kept_zones": sorted(names.get(k[0], str(k[0])) for k, _ in leaves if kept(k)),
                "silenced_zones": sorted(names.get(k[0], str(k[0])) for k, _ in leaves if not kept(k)),
                "zone_playlists": sorted({v for _, (_, v) in leaves})}
        slots.append(slot)
        for _, v in leaves:
            if v != pid:
                freed_tracks |= subtree_tracks(v)
        for sid in unreached:
            freed_tracks |= subtree_tracks(sid)
        freed_tracks |= {t["id"] for t in loop["tracks"]}

    # media ids whose every user (in every bank) is now unreachable or repointed
    def free(mid):
        users = usage.get(str(mid), [])
        return users and all(bk == f"{BANK_ID}.bnk" and kind == "CAkMusicTrack" and uid in freed_tracks
                             for bk, uid, kind in users)
    # prefer the loops' own stems, then anything else that became unreachable
    order = [t["id"] for s in slots for t in s["loop"]["tracks"]]
    order += sorted(freed_tracks - set(order))
    pool = []
    for tid in order:
        for _, mid in track(b, tid)["srcs"]:
            if free(mid) and mid not in pool:
                pool.append(mid)
    need = len(slots) + 1
    if len(pool) < need:
        raise RuntimeError(f"only {len(pool)} free media ids for {need} files")
    return slots, pool, freed_tracks


# --------------------------------------------------------------------------
# audio

def run(args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, errors="ignore", **kw)
    if r.returncode:
        raise RuntimeError(f"{os.path.basename(args[0])}: {r.stderr[-500:]}")
    return r

def vgm_info(path):
    out = run([config.VGMSTREAM, "-m", path]).stdout
    get = lambda k: re.search(rf"^{k}: (\d+)", out, re.M)
    return {"rate": int(get("sample rate").group(1)),
            "total": int(get("stream total samples").group(1)),
            "loop": (int(get("loop start").group(1)), int(get("loop end").group(1)))
                    if get("loop start") else None}

def render_loop(track_id, out_wav):
    """Render a library track as one seamless loop body at 48 kHz stereo."""
    lib = {t["id"]: t for t in listen.tracks_bl2() + listen.tracks_bl3()}
    if track_id not in lib:
        raise RuntimeError(f"unknown track {track_id}")
    tmp = out_wav + ".parts"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    pieces = []
    for i, (txtp, _) in enumerate(lib[track_id]["parts"]):
        info = vgm_info(txtp)
        if info["rate"] != RATE:
            raise RuntimeError(f"{txtp}: {info['rate']} Hz, expected {RATE}")
        wav = os.path.join(tmp, f"{i:03d}.wav")
        # wwiser boosts quiet Wwise music by up to +18 dB, and vgmstream's 16-bit
        # output clips dense BL3 layer stacks; render 6 dB down (the level is
        # matched to the slot afterwards anyway). The copy sits beside the
        # original so its relative .wem paths still resolve.
        quiet = txtp[:-5] + f".quiet{os.getpid()}.txtp"
        open(quiet, "w", encoding="utf-8").write(
            open(txtp, encoding="utf-8").read() + "\ncommands = #v 0.5\n")
        try:
            run([config.VGMSTREAM, "-i", "-o", wav, quiet])
        finally:
            os.remove(quiet)
        ls, le = info["loop"] or (0, info["total"])
        if (ls, le) != (0, info["total"]):
            # concat's inpoint/outpoint cut on packet boundaries, not samples
            raise RuntimeError(f"{txtp}: loop {ls}-{le} is not the whole stream; "
                               "needs sample-exact trimming before it can be used")
        pieces.append((wav, ls, le))
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        for wav, ls, le in pieces:
            fh.write(f"file '{wav}'\n")
    run([config.FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
         "-ac", "2", "-c:a", "pcm_f32le", out_wav])
    shutil.rmtree(tmp, ignore_errors=True)
    samples = sum(le - ls for _, ls, le in pieces)
    return samples

def lufs(path):
    r = run([config.FFMPEG, "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=framelog=quiet",
             "-f", "null", "-"])
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1])

def encode(src_wav, wem, gain_db):
    af = f"volume={gain_db:.2f}dB,alimiter=limit=0.89:level=false"
    return wemopus.encode(src_wav, wem, KBPS, af=af)

def silence_wem(path, seconds):
    wav = path + ".wav"
    run([config.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
         f"anullsrc=r={RATE}:cl=stereo", "-t", f"{seconds:.3f}", wav])
    info = wemopus.encode(wav, path, 16)
    os.remove(wav)
    return info


# --------------------------------------------------------------------------
# patching

class Patcher:
    def __init__(self, path):
        self.data = bytearray(open(path, "rb").read())
        self.log = []

    def put(self, field, fmt, value, what):
        off, old = field
        cur = struct.unpack_from(fmt, self.data, off)[0]
        if fmt in ("<I", "<i", "<H") and cur != old:
            raise RuntimeError(f"{what}: expected {old} at {off}, found {cur}")
        if fmt == "<f" and abs(cur - old) > 1e-4 * max(1.0, abs(old)):
            raise RuntimeError(f"{what}: expected {old} at {off}, found {cur}")
        if any(e["offset"] == off for e in self.log):
            raise RuntimeError(f"{what}: offset {off} patched twice")
        struct.pack_into(fmt, self.data, off, value)
        self.log.append({"offset": off, "old": old, "new": value, "what": what})

SYNC = {"Immediate": 0, "NextGrid": 1, "NextBar": 2, "NextBeat": 3, "ExitMarker": 7}

def plan_transitions(spec, b):
    """Rule edits: [(field, fmt, value, what)] for each spec'd transition rule."""
    out = []
    for t in spec.get("transitions", []):
        hit = None
        for r in b.objects(b.obj[t["container"]], "AkMusicTransitionRule"):
            if ([v for _, v in b.fields(r, "srcID")] == t["src"] and
                    [v for _, v in b.fields(r, "dstID")] == t["dst"]):
                hit = r
        if hit is None:
            raise RuntimeError(f"no rule {t['src']} -> {t['dst']} in {t['container']}")
        times = b.fields(hit, "transitionTime")            # [source fade, destination fade]
        what = f"rule {t['src']}->{t['dst']} of {t['container']}"
        out.append((b.fields(hit, "eSyncType")[0], "<I", SYNC[t["sync"]], f"{what}: sync {t['sync']}"))
        out.append((times[0], "<i", t["fade_out_ms"], f"{what}: fade out"))
        out.append((times[1], "<i", t["fade_in_ms"], f"{what}: fade in"))
    return out

LAST_EXIT = 4        # AkEntryType: sync the destination to where it last stopped

def plan_resume(spec, b, slots):
    """Combat music picks up where it stopped instead of restarting.

    In every listed container, the rules that start a track ("any -> any",
    "nothing -> any") sync the destination to its last exit position, the way
    BL4's own ambient container does, with a short fade-in since that lands
    mid-phrase. A track never played yet still starts at its entry cue. The
    exploration track of a biome slot resumes too ("any -> riser" rule), so a
    fight does not send it back to its opening."""
    r = spec.get("resume")
    if not r:
        return []
    risers = {s["container"]: s["playlist"] for s in slots if s["kind"] == "biome"}
    out = []
    for cid in r["containers"]:
        hits = 0
        for rule in b.objects(b.obj[cid], "AkMusicTransitionRule"):
            src = [v for _, v in b.fields(rule, "srcID")]
            dst = [v for _, v in b.fields(rule, "dstID")]
            what = f"rule {src}->{dst} of {cid}"
            entry = b.fields(rule, "eEntryType")[0]
            if dst == [-1] and src in ([-1], [0]):
                out.append((entry, "<H", LAST_EXIT, f"{what}: resume at last exit"))
                out.append((b.fields(rule, "transitionTime")[1], "<i", r["fade_in_ms"], f"{what}: fade in"))
                hits += 1
            elif cid in risers and dst == [risers[cid]]:
                out.append((entry, "<H", LAST_EXIT, f"{what}: exploration resumes at last exit"))
        if not hits:
            raise RuntimeError(f"resume: no rule starting tracks in container {cid}")
    return out

def apply(p, slots, silence, lengths, rule_edits=()):
    for field, fmt, value, what in rule_edits:
        p.put(field, fmt, value, what)
    for s in slots:
        name = f"{s['slot']} {s.get('ctype', '')}".strip()
        for off_val, target, what in s["repoint_leaves"]:
            p.put(off_val, "<I", target, f"{name}: {what}")
        if s.get("rekey"):
            field, key, what = s["rekey"]
            p.put(field, "<I", key, f"{name}: {what}")
        for kind, points in s.get("flatten") or ():
            for pt in points:
                p.put(pt["To"], "<f", FLAT[kind], f"{name}: clip automation {kind} -> unity")
        for param, rtpc, points in s.get("curves") or ():
            for pt in points:
                p.put(pt["To"], "<f", RTPC_NEUTRAL[param], f"{name}: RTPC {rtpc} curve on param {param} -> neutral")
        if s.get("bus") and s["bus"][0][1] != s["bus"][1]:
            p.put(s["bus"][0], "<I", s["bus"][1], f"{name}: output bus -> {s['bus'][1]}")
        loop = s["loop"]
        for field in s["items"]:
            if field[1] != loop["id"]:
                p.put(field, "<I", loop["id"], f"{name}: playlist item -> loop segment")
        ms = lengths[s["media"]]
        p.put(loop["dur"], "<d", ms, f"{name}: segment length")
        for m in loop["marks"]:
            mid = m["id"][1]
            pos = 0.0 if mid == ENTRY_CUE else ms if mid == EXIT_CUE else min(m["fPosition"][1], ms)
            p.put(m["fPosition"], "<d", pos, f"{name}: marker {mid}")
        for t in loop["tracks"]:
            mid = s["media"] if t["id"] == s["host"]["id"] else silence
            for f in t["srcs"]:
                p.put(f, "<I", mid, f"{name}: track {t['id']} source")
            for c in t["clips"]:
                p.put(c["sourceID"], "<I", mid, f"{name}: track {t['id']} clip source")
                if t["id"] == s["host"]["id"]:
                    p.put(c["fPlayAt"], "<d", 0.0, f"{name}: host clip start")
                    p.put(c["fBeginTrimOffset"], "<d", 0.0, f"{name}: host clip trim in")
                    p.put(c["fEndTrimOffset"], "<d", 0.0, f"{name}: host clip trim out")
                    p.put(c["fSrcDuration"], "<d", ms, f"{name}: host clip source length")


# --------------------------------------------------------------------------
# verification with wwiser

def wwiser(args, cwd):
    return run([sys.executable, bnktree.WWISER] + args, cwd=cwd)

def gen_txtp(bank_dir, out, filt, extra=()):
    shutil.rmtree(os.path.join(bank_dir, out), ignore_errors=True)
    # the bank goes first: -gf takes every argument after it as a filter item
    wwiser([f"{BANK_ID}.bnk", "-d", "none", "-g", "-go", out, "-gw", "wem"] + list(extra) +
           ["-gf"] + filt, cwd=bank_dir)
    return os.path.join(bank_dir, out)

def playlist_txtps(bank_dir, out, ids, b):
    """Render-ready TXTP per playlist id, at unity master volume so levels compare."""
    d = gen_txtp(bank_dir, out, [str(i) for i in ids], ["-gv", "1.0"])
    by_index = {}
    for f in os.listdir(d):
        m = re.search(r"-(\d+)-musicranseq", f)
        if m and f.endswith(".txtp"):
            by_index.setdefault(int(m.group(1)), os.path.join(d, f))
    return {i: by_index[b.obj[i].get_attr("index")] for i in ids
            if b.obj[i].get_attr("index") in by_index}

def link_media(dst, overrides):
    os.makedirs(dst, exist_ok=True)
    for f in os.listdir(ORIG_WEM):
        p = os.path.join(dst, f)
        if not os.path.exists(p):
            os.link(os.path.join(ORIG_WEM, f), p)
    for mid, src in overrides.items():
        p = os.path.join(dst, f"{mid}.wem")
        if os.path.exists(p):
            os.remove(p)              # never write through a hard link to the originals
        shutil.copyfile(src, p)

def render(txtp, wav, loops=1):
    run([config.VGMSTREAM, "-l", str(loops), "-f", "5", "-o", wav, txtp])

def path_index(d):
    """Every music path of a -gd TXTP folder, keyed by event + gamesyncs (file
    names are not stable: wwiser renames them as duplicate relationships move).
    Value: (playback body, object ids on the path, media ids played)."""
    out = {}
    for f in os.listdir(d):
        if not f.endswith(".txtp"):
            continue
        txt = open(os.path.join(d, f), encoding="utf-8").read()
        body, _, notes = txt.partition("# AUTOGENERATED")
        ev = re.search(r"hashname: (\S+)", notes)
        gs = re.search(r"# \* gamesyncs: (.*)", notes)
        extra = re.search(r"~\{([a-z]+)", f)
        key = (ev.group(1) if ev else f.split(" ")[0], gs.group(1).strip() if gs else "",
               extra.group(1) if extra else "")
        ids = {int(x) for x in re.findall(r"CAk\w+\[\d+\] (\d+)", notes)}
        out[key] = (body, ids, set(re.findall(r"wem/(\d+)\.wem", body)))
    return out

def verify_paths(orig_dir, mod_dir, touched, written_media, accepted=()):
    """Every path whose playback changed must run through an object the mod
    patched, and nothing else may play the media the mod overwrote.
    `accepted`: key substrings the spec explicitly accepts losing (with a reason)."""
    gen_txtp(orig_dir, "txtp_all", ["mus_*"], ["-gd"])
    gen_txtp(mod_dir, "txtp_all", ["mus_*"], ["-gd"])
    A = path_index(os.path.join(orig_dir, "txtp_all"))
    B = path_index(os.path.join(mod_dir, "txtp_all"))
    # -gd still folds some identical paths together, so also compare against the
    # plain original run, which lists them under their own names
    plain = path_index(config.data("bl4", "txtp"))
    bodies_a = {v[0] for v in A.values()} | {v[0] for v in plain.values()}
    bodies_b = {v[0] for v in B.values()}
    ours = lambda ids: bool(ids & touched)
    ok = lambda k: any(a in " ".join(k) for a in accepted)
    changed = [k for k in A.keys() & B.keys() if A[k][0] != B[k][0]]
    stray = [k for k in changed if not (ours(A[k][1]) or ours(B[k][1]))]
    # paths present on one side only: fine if ours, or if the same playback
    # exists on the other side under another key (a dedupe artefact)
    stray += [k for k in A.keys() - B.keys() if not ours(A[k][1]) and A[k][0] not in bodies_b]
    stray += [k for k in B.keys() - A.keys() if not ours(B[k][1]) and B[k][0] not in bodies_a]
    accepted_hits = [list(k) for k in stray if ok(k)]
    stray = [k for k in stray if not ok(k)]
    leaks = [k for k, v in B.items() if v[2] & written_media and not ours(v[1])]
    return {"paths_before": len(A), "paths_after": len(B), "changed": len(changed),
            "accepted": accepted_hits,
            "stray": [list(k) for k in stray], "media_leaks": [list(k) for k in leaks]}


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    spec = json.load(open(a.spec))
    os.makedirs(WORK, exist_ok=True)
    usage = json.load(open(config.data("bl4", "media_usage.json")))

    print("reading the bank with wwiser...")
    b = bnktree.Bank(SRC_BANK)
    srcs = source_fields(b)
    n, bad = check_prefetch(open(SRC_BANK, "rb").read(), srcs, {})
    if bad:
        raise RuntimeError(f"{len(bad)} of {n} shipped sources break the preload rule "
                           f"(e.g. {bad[0]}); re-derive wemopus.prefetch_size first")
    print(f"  preload rule holds for all {n} music sources of the shipped bank")
    slots, pool, freed = plan(spec, b, usage)
    resume = plan_resume(spec, b, slots)
    rule_edits = plan_transitions(spec, b) + resume
    threat = None
    if spec.get("threat"):                    # game-side: when combat music ends
        ncs_file, threat = musicncs.build(spec["threat"]["light_exit"], config.data("ncs", "work"))
        print(f"  {musicncs.FILE} ({threat['source']}): light combat exits below "
              f"{threat['new_exit']:g} instead of {threat['exit']:g} (enters at {threat['enter']:g})")
    for s in slots:
        extra = ""
        if s["kind"] == "zone":
            extra = f"  ({len(s['kept_zones'])} zones keep a track, {len(s['silenced_zones'])} play nothing)"
        elif s["kind"] == "biome":
            extra = (f"  (rising leaf re-keyed, {sum(len(p) for _, p in s['flatten'])} fade points flattened, "
                     f"{len(s['curves'])} threat curves made neutral"
                     + (f", bus {s['bus'][0][1]} -> {s['bus'][1]})" if s["bus"] else ")"))
        print(f"  {s['slot'][:28]:28s} {s.get('ctype', s['kind']):9s} playlist {s['playlist']} "
              f"loop {s['loop']['id']} host track {s['host']['id']}" + extra)
    for _, _, _, what in rule_edits:
        if not any(what is w for _, _, _, w in resume):
            print(f"  {what}")
    if resume:
        print(f"  resume at last exit: {sum('resume' in w for _, _, _, w in resume)} rules "
              f"in {len(spec['resume']['containers'])} containers")
    print(f"  {len(pool)} free media ids; {len(freed)} tracks become unreachable or repointed")
    if a.dry_run:
        for s in slots:
            if s["kind"] == "zone":
                print(f"  {s['slot']}: keeps {s['kept_zones']}")
        return

    media_dir = os.path.join(WORK, "media")
    orig_dir = os.path.join(WORK, "orig")
    mod_dir = os.path.join(WORK, "bank")
    for d in (media_dir, orig_dir, mod_dir):
        os.makedirs(d, exist_ok=True)
    names_txt = config.data("bl4", "wwnames.txt")
    shutil.copyfile(SRC_BANK, os.path.join(orig_dir, f"{BANK_ID}.bnk"))
    for d in (orig_dir, mod_dir):
        shutil.copyfile(names_txt, os.path.join(d, "wwnames.txt"))

    # 1. the level each slot's own music plays at (unity master volume)
    print("rendering the original cues for their loudness...")
    want = sorted({s["playlist"] for s in slots} |
                  {z for s in slots if s.get("zone_playlists") for z in s["zone_playlists"]})
    orig = playlist_txtps(orig_dir, "txtp_level", want, b)
    link_media(os.path.join(orig_dir, "txtp_level", "wem"), {})
    def level(pid):
        wav = os.path.join(media_dir, f"orig_{pid}.wav")
        render(orig[pid], wav)
        return pid, lufs(wav)
    with ThreadPoolExecutor(12) as ex:
        orig_lufs = dict(ex.map(level, [p for p in want if p in orig]))
    for s in slots:
        if s.get("zone_playlists"):             # zones vary, so aim for the container's median
            vals = sorted(orig_lufs[z] for z in s["zone_playlists"] if z in orig_lufs)
            s["target"] = vals[len(vals) // 2]
        else:
            s["target"] = orig_lufs[s["playlist"]]

    # 2. the new music: one seamless loop body per slot
    print("rendering the new music as loop bodies...")
    sources = sorted({s["source"] for s in slots})      # slots may share a source
    def body(src):
        wav = os.path.join(media_dir, re.sub(r"[^A-Za-z0-9]+", "_", src) + ".body.wav")
        n = render_loop(src, wav)
        return src, (wav, n, lufs(wav))
    with ThreadPoolExecutor(8) as ex:
        rendered = dict(ex.map(body, sources))
    bodies = [(s,) + rendered[s["source"]] for s in slots]
    sil_need = 1.0 + max(c["fSrcDuration"][1] for s in slots for t in s["loop"]["tracks"]
                         if t["id"] != s["host"]["id"] for c in t["clips"]) / 1000.0
    # media ids: the biggest headers first, silence last (a silent track that
    # fails to start is still silent)
    wanted = sorted(((i, prefetch_estimate(n, KBPS)) for i, (_, _, n, _) in enumerate(bodies)),
                    key=lambda kv: -kv[1])
    wanted.append(("silence", prefetch_estimate(int(sil_need * RATE), 16)))
    assigned = assign_media(wanted, pool, srcs)
    silence = assigned["silence"][0]
    lengths = {}
    for i, (s, wav, n, src_lufs) in enumerate(bodies):
        s["media"], s["capacity"] = assigned[i]
        s.update(body=wav, samples=n, gain_db=round(s["target"] - src_lufs, 2),
                 wem=os.path.join(media_dir, f"{s['media']}.wem"))
        lengths[s["media"]] = n * 1000.0 / RATE
        print(f"  {s['slot'][:28]:28s} {s.get('ctype', s['kind']):9s} -> media {s['media']} "
              f"(its original preload {s['capacity']:,} B)")
    print(f"  silence -> media {silence}")
    sil_wem = os.path.join(media_dir, f"{silence}.wem")
    silence_wem(sil_wem, sil_need)

    # 3. patch, then let wwiser re-read the result
    p = Patcher(SRC_BANK)
    apply(p, slots, silence, lengths, rule_edits)
    mod_bank = os.path.join(mod_dir, f"{BANK_ID}.bnk")
    open(mod_bank, "wb").write(p.data)
    json.dump(p.log, open(os.path.join(WORK, "patch_log.json"), "w"), indent=1)
    print(f"patched {len(p.log)} fields; bank size unchanged ({len(p.data):,} B)")
    m = bnktree.Bank(mod_bank)
    if m.kind != b.kind:
        raise RuntimeError("patched bank does not parse to the same objects")
    for s in slots:
        got = (m.field1(m.obj[s["loop"]["id"]], "fDuration")[1],
               own(m.objects(m.obj[s["host"]["id"]], "AkMediaInformation")[0])["sourceID"][1])
        if got != (lengths[s["media"]], s["media"]):
            raise RuntimeError(f"{s['slot']}: read-back mismatch {got}")
    print("wwiser re-reads the patched bank: same objects, patched values in place")

    # 4. encode, then measure the patched cue itself and correct the gain once
    overrides = {s["media"]: s["wem"] for s in slots}
    overrides[silence] = sil_wem
    mod_level = playlist_txtps(mod_dir, "txtp_level", [s["playlist"] for s in slots], m)
    def encode_and_measure(s):
        info = encode(s["body"], s["wem"], s["gain_db"])
        if info["samples"] != s["samples"]:
            raise RuntimeError(f"{s['source']}: encoded {info['samples']} samples, loop has {s['samples']}")
        return s
    todo = list(slots)
    for attempt in range(1, 5):
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(encode_and_measure, todo))
        link_media(os.path.join(mod_dir, "txtp_level", "wem"), overrides)
        def measured(s):
            wav = os.path.join(media_dir, f"mod_{s['playlist']}.wav")
            render(mod_level[s["playlist"]], wav)
            return s, lufs(wav)
        with ThreadPoolExecutor(8) as ex:
            res = dict((s["playlist"], (s, got)) for s, got in ex.map(measured, todo))
        for pid, (s, got) in res.items():
            s["off"] = s["target"] - got
        todo = [s for s in slots if abs(s["off"]) > 0.5]
        if not todo or attempt == 4:
            break
        for s in todo:          # the limiter makes gain non-linear, so iterate
            s["gain_db"] = round(s["gain_db"] + s["off"], 2)
    off = [(s, s["off"]) for s in slots]
    for s, d in off:
        s["result_lufs"] = round(s["target"] - d, 1)
        print(f"  {s['slot'][:26]:26s} {s.get('ctype', 'zones'):9s} {s['source']:40s} "
              f"{s['samples'] / RATE:7.2f}s  {s['result_lufs']:6.1f} LUFS (original {s['target']:.1f})")

    # 4b. every source that streams a written file - hosts, silenced stems and
    #     unreachable leftovers alike - declares that file's own preload size.
    #     v2 kept the old files' sizes: wherever the new header was bigger, the
    #     game preloaded half a header and the track stayed silent.
    sizes = {mid: wemopus.prefetch_size(path) for mid, path in overrides.items()}
    owner = {s["media"]: f"{s['slot']} {s.get('ctype', '')}".strip() for s in slots}
    owner[silence] = "silence"
    for src in srcs:
        mid = struct.unpack_from("<I", p.data, src["source"][0])[0]
        if mid in sizes:
            p.put(src["inmem"], "<I", sizes[mid], f"{owner[mid]}: track {src['track']} preload size")
    for s in slots:
        s["prefetch"] = sizes[s["media"]]
    open(mod_bank, "wb").write(p.data)
    json.dump(p.log, open(os.path.join(WORK, "patch_log.json"), "w"), indent=1)
    n, bad = check_prefetch(p.data, srcs, overrides)
    if bad:
        json.dump(bad, open(os.path.join(WORK, "prefetch_bad.json"), "w"), indent=1)
        raise RuntimeError(f"{len(bad)} sources declare the wrong preload size - see prefetch_bad.json")
    short = [s for s in slots if s["capacity"] < s["prefetch"]]
    print(f"preload sizes: all {n} music sources match their files "
          f"({len(p.log)} fields patched in total); "
          f"{len(slots) - len(short)} of {len(slots)} new files also fit their media id's original size")

    # 5. every music path except the patched ones must generate the same TXTP
    gen_txtp(mod_dir, "txtp", ["mus_*"])
    link_media(os.path.join(mod_dir, "txtp", "wem"), overrides)
    before = config.data("bl4", "txtp")
    after = os.path.join(mod_dir, "txtp")
    names = lambda d: {f for f in os.listdir(d) if f.endswith(".txtp")}
    changed = sorted(f for f in names(before) & names(after)
                     if open(os.path.join(before, f), encoding="utf-8").read()
                     != open(os.path.join(after, f), encoding="utf-8").read())
    gone, new = sorted(names(before) - names(after)), sorted(names(after) - names(before))
    json.dump({"changed": changed, "gone": gone, "new": new}, open(os.path.join(WORK, "txtp_diff.json"), "w"), indent=1)
    # what the mod may change: the playlists it patched, the 'falling' playlists
    # it bypassed, and the zone containers (every leaf there is rewritten).
    # Not whole biome containers - their untouched leaves must stay identical.
    touched = ({s["playlist"] for s in slots} |
               {old for s in slots if s["kind"] == "combat" for (_, old), _, _ in s["repoint_leaves"]} |
               {z["container"] for z in spec.get("zones", [])})
    v = verify_paths(orig_dir, mod_dir, touched, {str(k) for k in overrides},
                     list(spec.get("accept_losing", {})))
    json.dump(v, open(os.path.join(WORK, "verify.json"), "w"), indent=1)
    print(f"music paths (duplicates kept): {v['changed']} of {v['paths_before']} changed, "
          f"all through patched objects; {len(v['accepted'])} accepted losses, "
          f"{len(v['stray'])} stray, {len(v['media_leaks'])} media leaks")
    if v["stray"] or v["media_leaks"]:
        raise RuntimeError("the patch reaches music it was not asked to change - see verify.json")

    # 6. pak
    pak_spec ={"replace": {str(k): v for k, v in overrides.items()},
                "files": {f"OakGame/Content/WwiseAudio/{BANK_ID}.bnk": mod_bank}}
    if threat:
        pak_spec["files"][musicncs.PAK_PATH] = ncs_file
    json.dump(pak_spec, open(os.path.join(WORK, "pak_spec.json"), "w"), indent=1)
    out = os.path.join(HERE, "..", "build")
    run([sys.executable, os.path.join(HERE, "build_override.py"), os.path.join(WORK, "pak_spec.json"),
         spec["name"], out])
    print(f"built {spec['name']} in {os.path.normpath(out)}")
    if a.install:
        for ext in ("pak", "utoc", "ucas"):
            shutil.copyfile(os.path.join(out, f"{spec['name']}.{ext}"),
                            os.path.join(config.PAKS, f"{spec['name']}.{ext}"))
        print(f"installed into {config.PAKS}")

    json.dump({"name": spec["name"], "silence": silence, "changed": changed, "gone": gone,
               "slots": [{k: v for k, v in s.items() if k in
                          ("kind", "slot", "ctype", "container", "page", "source", "playlist",
                           "media", "prefetch", "capacity", "samples", "target", "result_lufs",
                           "gain_db", "zones", "kept_zones", "silenced_zones")} |
                         {"loop": s["loop"]["id"], "host": s["host"]["id"]} for s in slots],
               "rules": [what for _, _, _, what in rule_edits], "threat": threat,
               "resume": spec.get("resume")},
              open(os.path.join(WORK, "result.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
