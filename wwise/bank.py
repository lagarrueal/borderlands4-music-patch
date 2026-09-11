"""Wwise v145 SoundBank reader - enough of HIRC to map music cues to .wem files.

Strategy: read DirectParentID out of every music node (it sits at a small,
computable offset inside NodeBaseParams) and walk *upwards* from tracks. That
avoids parsing the fully variable Children/StateChunk/RTPC tails.
"""
import struct, collections

T_SEGMENT, T_TRACK, T_MSWITCH, T_MRANSEQ = 10, 11, 12, 13

def fnv(name):
    h = 2166136261
    for c in name.lower().encode('utf-8'):
        h = ((h * 16777619) & 0xFFFFFFFF) ^ c
    return h

def sections(b):
    o = 0
    while o + 8 <= len(b):
        tag = b[o:o+4].decode('ascii', 'replace')
        sz, = struct.unpack_from("<I", b, o+4)
        yield tag, o+8, sz
        o += 8 + sz

def hirc(b):
    for tag, off, sz in sections(b):
        if tag != "HIRC": continue
        n, = struct.unpack_from("<I", b, off); p = off + 4
        for _ in range(n):
            t = b[p]
            osz, = struct.unpack_from("<I", b, p+1)
            oid, = struct.unpack_from("<I", b, p+5)
            yield t, oid, b[p+5:p+5+osz]
            p += 5 + osz

def _node_base_parent(pl, o):
    """Skip NodeInitialFxParams etc. and return (DirectParentID, offset_after)."""
    o += 1                              # bIsOverrideParentFX
    nfx = pl[o]; o += 1
    if nfx:
        o += 1                          # bitsFXBypass
        o += nfx * 7                    # uFXIndex, fxID, bIsShareSet, bIsRendered
    o += 1                              # bIsOverrideParentMetadata
    nmeta = pl[o]; o += 1
    o += nmeta * 6                      # uFXIndex, fxID, bIsShareSet
    o += 1                              # bOverrideAttachmentParams
    o += 4                              # OverrideBusId
    parent, = struct.unpack_from("<I", pl, o); o += 4
    return parent, o

def parse_music_node(pl):
    """MusicSegment / MusicSwitchCntr / MusicRanSeqCntr: id, uFlags, NodeBaseParams."""
    parent, _ = _node_base_parent(pl, 5)
    return parent

def parse_track(pl):
    """MusicTrack: sources, then clip playlist/automation, then NodeBaseParams."""
    o = 4
    o += 1                              # uFlags
    nsrc, = struct.unpack_from("<I", pl, o); o += 4
    srcs = []
    for _ in range(nsrc):
        plugin, = struct.unpack_from("<I", pl, o); o += 4
        stype = pl[o]; o += 1
        sid, insize = struct.unpack_from("<II", pl, o); o += 8
        o += 1                          # uSourceBits
        if (plugin & 0x0F) == 2:
            sz, = struct.unpack_from("<I", pl, o); o += 4 + sz
        srcs.append((sid, stype))
    nplay, = struct.unpack_from("<I", pl, o); o += 4
    clips = []
    for _ in range(nplay):
        tid, sid, eid = struct.unpack_from("<III", pl, o); o += 12  # eventID: v>=140
        at, tb, te, dur = struct.unpack_from("<dddd", pl, o); o += 32
        clips.append((sid, at, dur))
    if nplay:
        o += 4                          # numSubTrack
    nauto, = struct.unpack_from("<I", pl, o); o += 4
    for _ in range(nauto):
        o += 4 + 4                      # uClipIndex, eAutoType
        npts, = struct.unpack_from("<I", pl, o); o += 4
        o += npts * 12
    parent, _ = _node_base_parent(pl, o)
    return srcs, clips, parent

def decision_tree(pl, group_ids):
    """Locate and decode a MusicSwitchCntr's AkDecisionTree.

    The tail is deterministic: uTreeDepth, group ids, group types, tree size,
    tree, eMode - so anchor on a known group id and validate the size.
    """
    for pos in range(0, len(pl) - 4):
        gid, = struct.unpack_from("<I", pl, pos)
        if gid not in group_ids: continue
        for depth in (1, 2, 3, 4):
            dpos = pos - 4
            if dpos < 0: continue
            d, = struct.unpack_from("<I", pl, dpos)
            if d != depth: continue
            tpos = pos + 4 * depth + depth          # groups + group types
            if tpos + 4 > len(pl): continue
            tsize, = struct.unpack_from("<I", pl, tpos)
            if tsize % 12 or tsize == 0: continue
            # ...uTreeDataSize, u8 eMode, then the tree itself
            if tpos + 4 + 1 + tsize != len(pl): continue
            groups = list(struct.unpack_from("<%dI" % depth, pl, pos))
            tree = pl[tpos+5: tpos+5+tsize]
            return groups, depth, tree
    return None

def walk_tree(tree, depth):
    """Yield (path_of_keys, audioNodeId) leaves of an AkDecisionTree."""
    nodes = [struct.unpack_from("<IIHH", tree, i*12) for i in range(len(tree)//12)]
    out = []
    def rec(idx, level, path):
        key, val, w, p = nodes[idx]
        if level == depth:
            out.append((tuple(path), val))
            return
        cidx, ccount = val & 0xFFFF, (val >> 16) & 0xFFFF
        for c in range(cidx, cidx + ccount):
            if c >= len(nodes): return
            rec(c, level + 1, path + [nodes[c][0]])
    root = nodes[0]
    cidx, ccount = root[1] & 0xFFFF, (root[1] >> 16) & 0xFFFF
    for c in range(cidx, cidx + ccount):
        rec(c, 1, [nodes[c][0]])
    return out


def decision_tree_auto(pl):
    """Find a MusicSwitchCntr's AkDecisionTree without knowing its group ids.

    Tail layout is: u8 bIsContinuePlayback, u32 uTreeDepth, u32 group[depth],
    u8 groupType[depth], u32 uTreeDataSize, u8 eMode, tree[uTreeDataSize].
    Anchor by requiring the whole thing to land exactly on the payload end.
    """
    n = len(pl)
    for depth in (1, 2, 3, 4):
        tpos_rel = 4 + 4*depth + depth          # depth field + groups + types
        pos = n - 1 - 4 - tpos_rel              # candidate start of uTreeDepth
        # tree size is whatever remains; solve directly
        for dpos in range(5, n - tpos_rel - 5):
            d, = struct.unpack_from("<I", pl, dpos)
            if d != depth: continue
            spos = dpos + tpos_rel
            if spos + 5 > n: continue
            tsize, = struct.unpack_from("<I", pl, spos)
            if tsize == 0 or tsize % 12 or spos + 5 + tsize != n: continue
            tree = pl[spos+5: n]
            key0, val0, w0, p0 = struct.unpack_from("<IIHH", tree, 0)
            if key0 != 0 or (val0 >> 16) == 0: continue
            groups = list(struct.unpack_from("<%dI" % depth, pl, dpos+4))
            return groups, depth, tree
    return None
