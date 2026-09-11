"""Build a BL4 .wem override mod: pak + the stub .utoc/.ucas that makes it mount.

  python build_override.py <spec.json> <Name_9700_P> [outdir]

spec.json is either
  {"replace": {"<wem id>": "<path to replacement .wem>", ...}}
or, to point whole cues at one file,
  {"cues": {"mus_combat_type:<0> + mus_biome:grasslands": "<path>.wem"}}

Replacement files must be real .wem. BL4 ships only Wwise Vorbis (0xffff) and
Wwise Opus (0x3041); reusing one of the game's own music .wem is the only
substitution proven to carry no codec risk.
"""
import sys, os, re, json, shutil, struct, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

MEDIA = os.path.join("OakGame", "Content", "WwiseAudio", "Media")

def wem_format(path):
    b = open(path, "rb").read(4096)
    if b[:4] != b"RIFF": return None
    o = 12
    while o + 8 <= len(b):
        cid = b[o:o+4]; csz, = struct.unpack_from("<I", b, o+4)
        if cid == b"fmt ": return struct.unpack_from("<H", b, o+8)[0]
        if csz == 0: break
        o += 8 + csz + (csz & 1)
    return None

def resolve(spec):
    """spec dict -> {wem_id: source_path}"""
    out = {}
    for wid, src in spec.get("replace", {}).items():
        out[int(wid)] = src
    if spec.get("cues"):
        cues = json.load(open(config.data("cue_to_wem.json")))
        for label, src in spec["cues"].items():
            if label not in cues:
                raise KeyError(f"no such cue: {label!r}")
            for wid in cues[label]["wems"]:
                out[wid] = src
    return out

def build(spec, name, outdir):
    mapping = resolve(spec)
    if not mapping: raise SystemExit("spec selected no files")
    stage = os.path.join(outdir, "stage")
    shutil.rmtree(stage, ignore_errors=True)
    media = os.path.join(stage, MEDIA)
    os.makedirs(media, exist_ok=True)
    fmts = {}
    for wid, src in sorted(mapping.items()):
        f = wem_format(src)
        if f is None:
            raise SystemExit(f"{src} is not a RIFF .wem")
        fmts[f] = fmts.get(f, 0) + 1
        shutil.copyfile(src, os.path.join(media, f"{wid}.wem"))
    os.makedirs(outdir, exist_ok=True)
    pak = os.path.join(outdir, name + ".pak")
    subprocess.run([config.REPAK, "pack", "--version", "V11",
                    "-m", "../../../", stage, pak], check=True,
                   stdout=subprocess.DEVNULL)
    # BL4 discovers mods by scanning for .utoc, so a lone .pak never mounts.
    empty = os.path.join(outdir, "_empty", "Windows")
    os.makedirs(empty, exist_ok=True)
    subprocess.run([config.RETOC, "to-zen", "--version", "UE5_5", empty,
                    os.path.join(outdir, name + ".utoc")], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.rmtree(stage, ignore_errors=True)
    shutil.rmtree(os.path.join(outdir, "_empty"), ignore_errors=True)
    return pak, len(mapping), fmts

if __name__ == "__main__":
    spec = json.load(open(sys.argv[1]))
    name = sys.argv[2]
    outdir = sys.argv[3] if len(sys.argv) > 3 else "build"
    if not re.search(r"_\d+_P$", name):
        raise SystemExit("mod name needs a priority number, e.g. Name_9700_P")
    pak, n, fmts = build(spec, name, outdir)
    NAMES = {0xffff: "Wwise Vorbis", 0x3041: "Wwise Opus"}
    print(f"{n} .wem replaced  ({', '.join(f'{NAMES.get(k, hex(k))}: {v}' for k, v in fmts.items())})")
    for ext in ("pak", "utoc", "ucas"):
        p = os.path.join(outdir, f"{name}.{ext}")
        print(f"  {os.path.basename(p):<32} {os.path.getsize(p):>12,} B")
    print(f"\nInstall: copy all three into\n  {config.PAKS}")
