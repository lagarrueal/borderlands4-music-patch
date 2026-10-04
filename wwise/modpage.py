"""Render the mod's "after" previews and write listen/mod.js for the picker page.

  python modpage.py            (after musicmod.py has built the mod)

Reads data/bl4mod/result.json and the patched bank's TXTP folder, renders one
preview per change exactly as the patched game assembles it, and maps every
affected BL4 library track to the change that replaced it.
"""
import datetime, json, os, re, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config, listen

MOD_TXTP = config.data("bl4mod", "bank", "txtp")

NOTES = [
    "Fadefields and Terminus Range: the exploration track now plays everywhere in the region, not just inside named places. Order and Ripper places keep it through their own zone music.",
    "Fights: the region's combat track takes over within about a second. When the fight ends it keeps playing through BL4's cool-down, then hands back to the exploration track with a 2 to 3 second crossfade.",
    "Region changes (for example walking into an Order base) now crossfade at the next bar instead of waiting for the end of the current loop.",
    "The pre-combat riser no longer plays in the Fadefields or the Terminus Range: the exploration track continues instead.",
    "Ripper areas A to D all use Windshear Waste. The Carcadia Burn ripper fortress counts as Ripper D.",
    "Combat music holds while enemies stay in range: it now ends below threat 1 instead of 4 (game data, every region). It still starts at the same point.",
    "When combat music comes back after a drop-out, it continues where it stopped instead of restarting, in every region and the Vaults. The Fadefields and Terminus Range exploration tracks also resume after a fight.",
    "The Murder Mystery mission's 'body drop' sting no longer plays.",
    "Fortresses, vault ambience, bosses and missions keep BL4's own music.",
]

def find_txtp(pattern):
    hits = sorted(f for f in os.listdir(MOD_TXTP) if f.endswith(".txtp") and re.search(pattern, f))
    if not hits:
        raise SystemExit(f"no patched cue matches {pattern}")
    return os.path.join(MOD_TXTP, hits[0])

def main():
    res = json.load(open(config.data("bl4mod", "result.json")))
    t = open(os.path.join(listen.OUT, "tracks.js"), encoding="utf-8").read()
    lib = {x["id"] for x in json.loads(t[t.index("["):t.rindex("]") + 1])}
    zone_slots = {s["page"]: s for s in res["slots"] if s["kind"] == "zone"}

    changes = {}           # (group, state) -> change
    for s in res["slots"]:
        if s["kind"] == "combat":
            group = "Ripper areas A to D" if s["page"].startswith("ripper") else s["slot"]
            state = "Combat" if s["ctype"] == "basic" else "Combat (critical: badass enemies)"
            cue = rf"\[mus_combat_type={s['ctype']}\]\[mus_gameplay_state=Combat\]"
            if s["page"] == "Mus_Ambient_Vaults":
                pattern = r"^Mus_Ambient_Vaults " + cue
            else:
                pattern = rf"\[mus_biome={s['page']}\] " + cue
            bl4 = [f"bl4/combat/{s['page']}_{s['ctype']}"]
        elif s["kind"] == "biome":
            region = s["slot"].split(" exploration")[0]
            group, state = region, "Exploration, everywhere in the region"
            biome = "grasslands" if s["page"].startswith("grasslands") else "mountain"
            pattern = rf"\[mus_biome={biome}\]\.txtp$"
            zones = zone_slots.get("gr_" if biome == "grasslands" else "mnt_", {})
            bl4 = [f"bl4/ambient/{z}" for z in zones.get("silenced_zones", [])]
        else:
            region = s["slot"].split(" exploration")[0]
            group, state = region, "Exploration in Order and Ripper places"
            kept = s.get("kept_zones") or []
            grp = "mus_ambience_zone_gr" if s["page"] == "gr_" else "mus_ambiance_zone_mnt"
            pattern = rf"^Mus_WorldP_DefaultGameplay .*\[{grp}=({'|'.join(kept)})\]"
            bl4 = [f"bl4/ambient/{z}" for z in kept]
        key = (group, state)
        c = changes.setdefault(key, {"slot": group, "state": state, "bl4": [], "source": s["source"],
                                     "pattern": pattern, "loop_s": round(s["samples"] / 48000, 1),
                                     "lufs": s["result_lufs"], "orig_lufs": round(s["target"], 1)})
        c["bl4"] += [i for i in bl4 if i in lib and i not in c["bl4"]]

    def render(item):
        key, c = item
        tid = "bl4mod/" + re.sub(r"[^A-Za-z0-9]+", "_", f"{c['slot']}_{c['state']}").strip("_")
        tr = {"id": tid, "parts": [(find_txtp(c["pattern"]), False)], "detail": ""}
        listen.render(tr, force=True)
        return key, tr["file"], tr["dur"]
    with ThreadPoolExecutor(8) as ex:
        for key, f, dur in ex.map(render, changes.items()):
            changes[key].update(after=f, after_dur=dur)
    out = []
    for c in changes.values():
        c.pop("pattern")
        out.append(c)
    mod = {"name": res["name"], "built": datetime.date.today().isoformat(),
           "changes": out, "notes": NOTES}
    with open(os.path.join(listen.OUT, "mod.js"), "w", encoding="utf-8") as fh:
        fh.write("window.MOD = " + json.dumps(mod, indent=1) + ";\n")
    for c in out:
        print(f"  {c['slot'][:30]:30s} {c['state'][:40]:40s} {len(c['bl4']):3d} BL4 tracks  after {c['after']} ({c['after_dur']:.0f}s)")

if __name__ == "__main__":
    main()
