"""When combat music stops: the game's threat thresholds (gbxmusic NCS).

BL4 keeps combat music playing while the summed threat of nearby enemies stays
above an exit threshold. The thresholds are NCS data in Music_Oak2_Common
(gbxmusic4.ncs), which every region's music def inherits:

  light combat  enters at 9, exits below 4   <- ends combat music
  heavy         enters at 26, exits below 20
  critical      enters at 15, exits below 7.5

An enemy's threat also shrinks with distance, down to 1/8 at the edge
(threatscaleatmaxdistance 0.125). So a fight whose enemies spread out a bit
drops below 4, the music fades out mid-fight and restarts when they come back.

This edits the light exit threshold in the newest copy of that file and
stores it uncompressed, ready to pack at Engine/Content/_NCS/. It uses the
shared NCS toolkit (../../scripts) and the instrumented bl4.exe (../../tools);
see ../../CLAUDE.md for why each step is done this way.
"""
import glob, json, os, re, struct, subprocess, sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))           # bl4-mods
SCRIPTS = os.path.join(ROOT, "scripts")
BL4 = os.path.join(ROOT, "tools", "bl4.exe")
FILE = "Nexus-Data-gbxmusic4.ncs"
PAK_PATH = f"Engine/Content/_NCS/{FILE}"
RECORD = "music_oak2_common"
ONLINE_PATCHES = os.path.expanduser(
    r"~\Documents\My Games\Borderlands 4\Saved\PersistentDownloadDir\Gearbox\Patch")
VALUE = re.compile(r'VALUE bitpos=(\d+) bits=\d+ raw=\d+ idx=\d+ val="(.*)"$')


def run(args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, errors="ignore", **kw)
    if r.returncode:
        raise RuntimeError(f"{os.path.basename(args[0])} {args[1:3]}: {r.stdout[-300:]} {r.stderr[-300:]}")
    return r


def newest_copy(work):
    """The game's own newest gbxmusic4.ncs, carved out of its pak.

    Found by grepping raw pak bytes, never `repak list`, which cannot read 60 of
    BL4's paks; mod paks are excluded, or the build would read its own output."""
    sys.path.insert(0, SCRIPTS)
    from carve_ncs import carve
    needle = FILE.encode()
    for p in glob.glob(os.path.join(ONLINE_PATCHES, "*", "*.pak")):
        if needle in open(p, "rb").read():
            raise SystemExit(f"{p} (an online patch) carries {FILE}; teach musicncs.py to use it")
    version = lambda f: int(re.search(r"_(\d+)_P\.pak$", f).group(1))
    paks = sorted((f for f in os.listdir(config.PAKS) if re.fullmatch(r"pakchunk4-Windows_\d+_P\.pak", f)),
                  key=version, reverse=True)
    for f in paks:
        pak = os.path.join(config.PAKS, f)
        if needle not in open(pak, "rb").read():
            continue
        out = os.path.join(work, "carved_" + f[:-4])
        found = [t for table, _, t in carve(Path(pak), Path(out)) if table == "gbxmusic"]
        if len(found) != 1:
            raise SystemExit(f"{f} lists {FILE} but {len(found)} gbxmusic tables were carved from it")
        return f, str(found[0])
    raise SystemExit(f"no game pak contains {FILE}")


def show(payload, as_json=False):
    return run([BL4, "ncs", "show"] + (["--json"] if as_json else []) + [payload]).stdout


def leaves(node, path=()):
    """Flatten bl4's JSON into {path: scalar} for an exact before/after diff."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from leaves(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from leaves(v, path + (i,))
    else:
        yield path, node


def light_exit_cell(payload, work):
    """Bit position of Music_Oak2_Common's lightcombatthreatrange.exitthreshold.

    The trace gives values with bit positions but no field names, and the show
    output gives names without positions, so the cell is the unique trace pair
    (enter, exit) equal to the shown light range within this record."""
    text = show(payload)
    rec = text[text.index(f"{RECORD} = {{"):]
    m = re.search(r"lightcombatthreatrange: \{\s*enterthreshold: (\S+)\s*exitthreshold: (\S+)", rec)
    if not m:
        raise SystemExit(f"{RECORD} has no lightcombatthreatrange with both thresholds")
    enter, exit_ = m.groups()
    env = dict(os.environ, NCS_TRACE="1")
    lines = subprocess.run([BL4, "ncs", "show", payload], capture_output=True, text=True,
                           errors="ignore", env=env).stderr.splitlines()
    open(os.path.join(work, "trace.txt"), "w", encoding="utf-8").write("\n".join(lines))
    vals, inside = [], False
    for line in lines:
        if line.startswith("ENTRY key="):
            inside = line.split('"')[1] == RECORD
        elif inside and (v := VALUE.match(line)):
            vals.append((int(v.group(1)), v.group(2)))
    hits = [vals[i + 1][0] for i in range(len(vals) - 1) if (vals[i][1], vals[i + 1][1]) == (enter, exit_)]
    if len(hits) != 1:
        raise SystemExit(f"found the light range ({enter}, {exit_}) {len(hits)} times in the {RECORD} trace")
    return hits[0], float(enter), float(exit_)


def read_payload(ncs):
    sys.path.insert(0, SCRIPTS)
    from ncs_decomp import decompress
    payload, how = decompress(ncs)
    if payload is None:
        raise SystemExit(f"{ncs}: {how}")
    return payload


def write_stored(payload):
    """The game rejects Oodle-recompressed .ncs: write it stored (flag 0)."""
    return b"\x01NCS" + struct.pack("<III", 0, len(payload), len(payload)) + payload


def build(new_exit, work):
    """Patch the light exit threshold; returns (stored .ncs path, summary)."""
    os.makedirs(work, exist_ok=True)
    pak, carved = newest_copy(work)
    payload = os.path.join(work, "gbxmusic4.payload.bin")
    open(payload, "wb").write(read_payload(carved))
    bitpos, enter, old = light_exit_cell(payload, work)
    if not 0 < new_exit < enter:
        raise SystemExit(f"light exit {new_exit} must be above 0 and below the enter threshold {enter}")
    edits = os.path.join(work, "edits.json")
    json.dump([{"bitpos": bitpos, "target": f"{new_exit:.6f}"}], open(edits, "w"))
    patched = os.path.join(work, "gbxmusic4.patched.bin")
    r = run([sys.executable, os.path.join(SCRIPTS, "ncs_multipatch.py"), payload, patched, edits])
    if "verify=OK" not in r.stdout:
        raise SystemExit(f"ncs_multipatch failed:\n{r.stdout}")
    # the only difference anywhere in the table must be that one value
    a = dict(leaves(json.loads(show(payload, True))))
    b = dict(leaves(json.loads(show(patched, True))))
    diff = {k: (a.get(k), b.get(k)) for k in a.keys() | b.keys() if a.get(k) != b.get(k)}
    want = {f"{old:.6f}", f"{new_exit:.6f}"}
    if len(diff) != 1 or set(next(iter(diff.values()))) != want or "exitthreshold" not in next(iter(diff)):
        raise SystemExit(f"patched gbxmusic4 differs unexpectedly: {diff}")
    out = os.path.join(work, FILE)
    open(out, "wb").write(write_stored(open(patched, "rb").read()))
    if read_payload(out) != open(patched, "rb").read():
        raise SystemExit("stored gbxmusic4.ncs does not read back as the patched payload")
    return out, {"source": pak, "bitpos": bitpos, "enter": enter, "exit": old, "new_exit": new_exit}


if __name__ == "__main__":
    path, info = build(float(sys.argv[1]), config.data("ncs", "work"))
    print(path, info)
