"""Render Borderlands music to MP3 previews for the picker page.

Turns wwiser's TXTP folders into one listening unit per area and music state:

  BL2  each map's `music_states` value: exploration / combat / boss
  BL3  each zone's music *section*, its parts played back to back - in game the
       OakMusic system walks a section's parts in order while threat stays in
       the section's range, so the suite is what a fight actually sounds like
  BL4  the cues a mod would replace: combat per biome, bosses, zone ambience

  python listen.py [bl2] [bl3] [bl4] [--jobs N] [--force]

Output goes to ../listen/: <game>/*.mp3 plus tracks.js, which index.html reads.
Every preview is gain-matched to -16 LUFS so areas and games compare fairly.
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "listen"))
WORK = config.data("listen_tmp")
FFPROBE = os.path.join(os.path.dirname(config.FFMPEG), "ffprobe.exe")
TARGET_LUFS = -16.0
MAX_SECS = 480          # longer suites are cut here, with a fade

# --------------------------------------------------------------------------
# naming

BL2_MAPS = {
    "SouthernShelf_P": "Southern Shelf", "Cove_P": "Southern Shelf - Bay",
    "Glacial_P": "Windshear Waste", "Ice_P": "Three Horns - Divide",
    "Frost_P": "Three Horns - Valley", "icecanyon_p": "Frostburn Canyon",
    "Fridge_P": "The Fridge", "tundraexpress_p": "Tundra Express",
    "TundraTrain_P": "End of the Line", "Interlude_P": "The Dust",
    "dam_p": "Bloodshot Stronghold", "DamTop_P": "Bloodshot Ramparts",
    "Grass_P": "Highlands", "Outwash_P": "Highlands - Outwash",
    "Grass_Cliffs_P": "Thousand Cuts", "Grass_Lynchwood_P": "Lynchwood",
    "caverns_p": "Caustic Caverns", "PandoraPark_P": "Wildlife Exploitation Preserve",
    "ResearchCenter_P": "Natural Selection Annex", "HyperionCity_P": "Opportunity",
    "Fyrestone_P": "Arid Nexus - Badlands", "Stockade_P": "Arid Nexus - Boneyard",
    "CraterLake_P": "Sawtooth Cauldron", "Ash_P": "Eridium Blight",
    "FinalBossAscent_P": "Hero's Pass", "Boss_Volcano_P": "Vault of the Warrior",
    "Boss_Cliffs_P": "The Bunker", "VOGChamber_P": "Control Core Angel",
    "Luckys_P": "The Holy Spirits", "HypInterlude_P": "Friendship Gulag",
    "Sanctuary_P": "Sanctuary", "SanctuaryAir_P": "Sanctuary (flying)",
    "SanctIntro_P": "Sanctuary (intro)", "TestingZone_P": "Digistruct Peak",
    "ThresherRaid_P": "Terramorphous Peak", "SouthpawFactory_P": "Southpaw Steam & Power",
    "BanditSlaughter_P": "Fink's Slaughterhouse", "CreatureSlaughter_P": "Creature Slaughter Dome",
    "Orchid_OasisTown_P": "Oasis (Scarlett)", "Orchid_Caves_P": "Hayter's Folly (Scarlett)",
    "Orchid_SaltFlats_P": "Wurmwater (Scarlett)", "Orchid_ShipGraveyard_P": "The Rustyards (Scarlett)",
    "Orchid_Spire_P": "Magnys Lighthouse (Scarlett)", "Orchid_WormBelly_P": "Leviathan's Lair (Scarlett)",
    "Orchid_Refinery_P": "Washburne Refinery (Scarlett)",
    "Iris_Hub_P": "Badass Crater (Torgue)", "Iris_Hub2_P": "The Forge (Torgue)",
    "Iris_DL1_P": "Torgue Arena (Torgue)", "Iris_DL1_TAS_P": "Torgue Arena Ring (Torgue)",
    "Iris_DL2_P": "Southern Raceway (Torgue)", "Iris_DL2_Interior_P": "The Beatdown (Torgue)",
    "Iris_DL3_P": "Pyro Pete's Bar (Torgue)", "Iris_Moxxi_P": "Badass Crater Bar (Torgue)",
    "Sage_Underground_P": "Hunter's Grotto (Hammerlock)", "Sage_RockForest_P": "Candlerakk's Crag (Hammerlock)",
    "Sage_PowerStation_P": "Ardorton Station (Hammerlock)", "Sage_Cliffs_P": "Scylla's Grove (Hammerlock)",
    "Sage_HyperionShip_P": "H.S.S. Terminus (Hammerlock)",
    "Village_P": "Flamerock Refuge (Dragon Keep)", "CastleKeep_P": "Dragon Keep (Dragon Keep)",
    "CastleExterior_P": "Hatred's Shadow (Dragon Keep)", "Dungeon_P": "Lair of Infinite Agony (Dragon Keep)",
    "DungeonRaid_P": "The Winged Storm (Dragon Keep)", "Dark_Forest_P": "The Forest (Dragon Keep)",
    "Dead_Forest_P": "Immortal Woods (Dragon Keep)", "Docks_P": "Unassuming Docks (Dragon Keep)",
    "Mines_P": "Mines of Avarice (Dragon Keep)", "TempleSlaughter_P": "Murderlin's Temple (Dragon Keep)",
    "OldDust_P": "Dahl Abandon (Lilith DLC)", "Helios_P": "Helios Fallen (Lilith DLC)",
    "GaiusSanctuary_P": "Mt. Scarab Research Center (Lilith DLC)", "BackBurner_P": "The Backburner (Lilith DLC)",
    "Sandworm_P": "The Burrows (Lilith DLC)", "SandwormLair_P": "Writhing Deep (Lilith DLC)",
    "Sanctuary_Hole_P": "Sanctuary Hole (Lilith DLC)",
    "Pumpkin_Patch_P": "Hallowed Hollow (Bloody Harvest)", "Hunger_P": "Gluttony Gulch (Wattle Gobbler)",
    "Xmas_P": "Marcus's Mercenary Shop", "Easter_P": "Wam Bam Island (Son of Crawmerax)",
    "Distillery_P": "Rotgut Distillery (Wedding Day Massacre)",
}
BL2_STATES = {          # music_states values, names recovered by FNV hash
    3271157729: ("explore", "Exploration"), 3944980085: ("combat", "Combat"),
    2041601160: ("boss", "Boss 1"), 2041601163: ("boss", "Boss 2"),
    2041601162: ("boss", "Boss 3"), 2041601167: ("boss", "Boss"),
}

BL3_AREAS = {
    "Prologue": "Covenant Pass", "City": "Meridian Metroplex", "Towers": "Lectra City",
    "Orbital_Platform": "Skywell-27", "Atlas_HQ": "Atlas HQ", "Monastery": "Athenas",
    "Prison": "The Anvil", "Mansion": "Jakobs Estate", "Marshfields": "Floodmoor Basin",
    "Desert": "Devil's Razor", "Mine": "Konrad's Hold", "Motorcade": "Carnivora",
    "Motorcade_Interior": "Carnivora (interior)", "Desolate": "Desolation's Edge",
    "Proving_Grounds_01": "Proving Grounds", "Sanctuary_3": "Sanctuary III",
    "Takedown2": "Guardian Takedown", "Raid01_Maliwan": "Maliwan Takedown",
    "Raid01_Maliwan_Bosses": "Maliwan Takedown (bosses)", "BloodyHarvest": "Bloody Harvest",
    "Cartels_Run_01": "Revenge of the Cartels 1", "Cartels_Run_02": "Revenge of the Cartels 2",
    "Cartels_Run_03": "Revenge of the Cartels 3", "Diamond_Loot": "Diamond Armory",
    "FrostSite": "Stormblind Complex (Director's Cut)",
}
BL3_DLC = {"Dandelion": "Moxxi's Heist", "Alisma": "Psycho Krieg", "Geranium": "Bounty of Blood"}
BL3_GLT = {"Lodge", "Village", "Woods", "Lake", "Venue", "Archive", "Bar", "Camp"}

BL4_BIOMES = {
    "grasslands": "Fadefields", "mountain": "Terminus Range", "shatterlands": "Carcadia Burn",
    "city": "Dominion", "ordera": "Order outpost A", "orderb": "Order outpost B",
    "orderC": "Order outpost C", "rippera": "Ripper camp A", "ripperb": "Ripper camp B",
    "ripperc": "Ripper camp C", "ripperD": "Ripper camp D",
}

def slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:90]

def bl3_area(event):
    key = re.sub(r"^MUS_|^Mus_", "", event)
    key = re.sub(r"_(Start|Play)$", "", key)
    m = re.match(r"(Dandelion|Alisma|Geranium)_(.+)", key)
    if m:
        return f"{BL3_DLC[m.group(1)]} - {m.group(2).replace('_', ' ')}"
    if key in BL3_GLT:
        return f"Guns, Love and Tentacles - {key}"
    return BL3_AREAS.get(key, key.replace("_", " "))

# --------------------------------------------------------------------------
# collecting tracks: each is {id, game, area, kind, label, detail, parts}
# where parts is a list of (txtp path, once) rendered back to back

def read_tags(txtp_dir):
    """short .txtp name -> wwiser's full title (txtp written with -te)."""
    path = os.path.join(txtp_dir, "!tags.m3u")
    if not os.path.exists(path):
        return {f: f[:-5] for f in os.listdir(txtp_dir) if f.endswith(".txtp")}
    out, cur = {}, None
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith("# %TITLE"):
            cur = line[len("# %TITLE"):].strip()
        elif line.endswith(".txtp") and cur:
            out[line] = cur
            cur = None
    return out

def is_music(path):
    return "CAkMusic" in open(path, encoding="utf-8", errors="ignore").read()

def tracks_bl2():
    d = config.data("bl2", "music", "txtp")
    out, seen = [], {}
    for f, title in sorted(read_tags(d).items()):
        if "~{" in title or not is_music(os.path.join(d, f)):
            continue
        m = re.match(r"(.+?)_SFX-\d+-\w+", title)
        mp = m.group(1) if m else title
        sw = re.search(r"\(1690668539=(\d+)\)", title)
        kind, label = BL2_STATES.get(int(sw.group(1)), ("other", "Theme")) if sw else ("other", "Theme")
        key = (mp, label)
        seen[key] = seen.get(key, 0) + 1
        n = seen[key]
        out.append({"id": f"bl2/{slug(mp)}/{slug(label)}{'' if n == 1 else n}",
                    "game": "BL2", "area": BL2_MAPS.get(mp, mp.replace("_", " ")),
                    "kind": kind, "label": label + ("" if n == 1 else f" ({n})"),
                    "detail": mp, "parts": [(os.path.join(d, f), False)]})
    return out

def tracks_bl3():
    d = config.data("bl3", "music", "txtp")
    sections = json.load(open(config.data("bl3", "sections.json")))
    suites, single, done = {}, [], set()
    for f, title in sorted(read_tags(d).items()):
        if "{s}=(" in title or "~{" in title or title in done:
            continue
        done.add(title)
        event = title.split(" ")[0]
        m = re.search(r"\(Mus_System_Sections=Mus_Section_(\d+)\)\(Mus_System_Parts=Mus_Part_(\d+)\)", title)
        if m:
            # one file per part: {r} variants of the same part would repeat it
            suites.setdefault((event, int(m.group(1))), {}).setdefault(int(m.group(2)), os.path.join(d, f))
        elif re.match(r"Mus_|MUS_", event):
            single.append((event, title, os.path.join(d, f)))
    out = []
    by_event = {}
    for event, sec in suites:
        by_event.setdefault(event, []).append(sec)
    for (event, sec), parts in sorted(suites.items()):
        parts = sorted(parts.items())
        data = sections.get(event, {}).get("sections", {})
        known = {int(k): v for k, v in data.items()}
        if sec in known:
            order = sorted(known, key=lambda k: (known[k][0], known[k][1]))
            rank = order.index(sec)
            cmin, cmax, _ = known[sec]
            if rank == 0:
                kind, label = "explore", "Exploration"
            else:
                n = len(order) - 1
                names = ["Combat"] if n == 1 else (["Combat (light)", "Combat (heavy)"] if n == 2
                         else ["Combat (light)"] + ["Combat (medium)"] * (n - 2) + ["Combat (heavy)"])
                kind, label = "combat", names[rank - 1]
            detail = f"threat {cmin:g}-{cmax:g}, {len(parts)} parts"
        else:
            secs = sorted(by_event[event])
            if len(secs) == 1:
                kind, label = "other", "Theme"
            elif sec == secs[0]:
                kind, label = "explore", "Exploration (by section order)"
            elif sec == secs[-1]:
                kind, label = "combat", "Combat (heavy, by section order)"
            else:
                kind, label = "combat", "Combat (by section order)"
            detail = f"section {sec}, {len(parts)} parts"
        out.append({"id": f"bl3/{slug(event)}/s{sec:02d}", "game": "BL3", "area": bl3_area(event),
                    "kind": kind, "label": label, "detail": detail,
                    "parts": [(p, True) for _, p in parts]})
    for event, title, path in single:
        if event in by_event:
            continue        # the event's sectioned music is already covered
        rest = title[len(event):].strip()
        boss = re.search(r"Boss|Phase|Fight", title)
        label = ("Boss" if boss else "Theme") + (f" {rest}" if rest else "")
        out.append({"id": f"bl3/{slug(event)}/{slug(rest) or 'main'}", "game": "BL3",
                    "area": bl3_area(event), "kind": "boss" if boss else "other",
                    "label": label, "detail": event, "parts": [(path, False)]})
    return out

def tracks_bl4():
    d = config.data("bl4", "txtp")
    out = []
    for f, title in sorted(read_tags(d).items()):
        if "~{" in title:
            continue
        path = os.path.join(d, f)
        event = title.split(" ")[0]
        if "mus_gameplay_state=Combat" in title:
            ct = re.search(r"mus_combat_type=(\w+)", title)
            ct = ct.group(1) if ct else "any"
            bm = re.search(r"mus_biome=(\w+)", title)
            key = bm.group(1) if bm else event
            area = BL4_BIOMES.get(key) or {"Mus_Ambient_Vaults": "Vaults",
                                            "Mus_Elpis_DefaultGameplay": "Elpis"}.get(key, key)
            out.append({"id": f"bl4/combat/{slug(key)}_{ct}", "game": "BL4", "area": area,
                        "kind": "combat", "label": f"Combat ({ct})",
                        "detail": "currently in BL4 - what a combat mod replaces",
                        "parts": [(path, False)]})
            continue
        m = re.match(r"Mus_WorldP_DefaultGameplay .*\[(mus_ambi[ae]nce_zone_\w+)=(\w+)\]( \{r\})?$", title)
        if m and "gameplay_state=Ambient" in title:
            out.append({"id": f"bl4/ambient/{m.group(2)}", "game": "BL4",
                        "area": m.group(1).split("_")[-1].upper(), "kind": "explore",
                        "label": m.group(2), "detail": "zone ambience, currently in BL4",
                        "parts": [(path, False)]})
            continue
        if re.search(r"boss", title, re.I) and not re.search(r"_stop|_exit", title, re.I):
            out.append({"id": f"bl4/boss/{slug(title)}", "game": "BL4", "area": "Bosses",
                        "kind": "boss", "label": title.split(" ")[0], "detail": title,
                        "parts": [(path, False)]})
    return out

# --------------------------------------------------------------------------
# rendering

def run(args):
    r = subprocess.run(args, capture_output=True, text=True, errors="ignore")
    if r.returncode:
        raise RuntimeError(f"{os.path.basename(args[0])} failed: {r.stderr[-400:]}")
    return r

def render(track, force=False):
    mp3 = os.path.join(OUT, track["id"].replace("/", os.sep) + ".mp3")
    track["file"] = os.path.relpath(mp3, OUT).replace(os.sep, "/")
    if os.path.exists(mp3) and not force:
        track["dur"] = duration(mp3)
        return track
    # ids can share a long prefix, so a truncated slug is not unique
    tmp = os.path.join(WORK, hashlib.sha1(track["id"].encode()).hexdigest()[:16])
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    wavs, skipped = [], 0
    for i, (txtp, once) in enumerate(track["parts"]):
        wav = os.path.join(tmp, f"{i:03d}.wav")
        try:
            run([config.VGMSTREAM] + (["-i"] if once else ["-l", "1", "-f", "5"]) + ["-o", wav, txtp])
        except RuntimeError:
            # a part can reference a .wem the game never shipped; keep the rest
            if len(track["parts"]) == 1:
                raise
            skipped += 1
            continue
        wavs.append(wav)
    if not wavs:
        raise RuntimeError("no part of this suite could be rendered")
    if skipped:
        track["detail"] += f" ({skipped} missing)"
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        for w in wavs:
            fh.write(f"file '{w}'\n")
    src = ["-f", "concat", "-safe", "0", "-i", lst]
    r = run([config.FFMPEG, "-hide_banner", "-nostats"] + src +
            ["-t", str(MAX_SECS), "-ac", "2", "-af", "ebur128=framelog=quiet", "-f", "null", "-"])
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    lufs = float(found[-1]) if found else TARGET_LUFS
    gain = 0.0 if lufs <= -69 else TARGET_LUFS - lufs
    total = sum(duration(w) for w in wavs)
    af = f"volume={gain:.2f}dB,alimiter=limit=0.97:level=false"
    if total > MAX_SECS:
        af += f",afade=t=out:st={MAX_SECS - 6}:d=6"
    os.makedirs(os.path.dirname(mp3), exist_ok=True)
    run([config.FFMPEG, "-y", "-hide_banner", "-nostats"] + src +
        ["-t", str(MAX_SECS), "-ac", "2", "-af", af, "-c:a", "libmp3lame", "-q:a", "3", mp3])
    shutil.rmtree(tmp, ignore_errors=True)
    track["dur"] = duration(mp3)
    track["lufs"] = round(lufs, 1)
    return track

def mean_volume(track):
    r = run([config.FFMPEG, "-hide_banner", "-nostats", "-i", os.path.join(OUT, track["file"]),
             "-af", "volumedetect", "-f", "null", "-"])
    m = re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr)
    return float(m.group(1)) if m else -91.0

def audible(tracks, jobs):
    """Drop silent placeholders and stingers - they only clutter the picker."""
    cache_path = config.data("listen_levels.json")
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    todo = [t for t in tracks if t["id"] not in cache]
    with ThreadPoolExecutor(jobs) as ex:
        for t, v in zip(todo, ex.map(mean_volume, todo)):
            cache[t["id"]] = v
    json.dump(cache, open(cache_path, "w"))
    return [t for t in tracks if t.get("dur", 0) >= 10 and cache.get(t["id"], -91) > -60]

def duration(path):
    r = run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
    return round(float(r.stdout.strip() or 0), 1)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("games", nargs="*", default=["bl2", "bl3", "bl4"])
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    collect = {"bl2": tracks_bl2, "bl3": tracks_bl3, "bl4": tracks_bl4}
    manifest = os.path.join(OUT, "tracks.js")
    old = []
    if os.path.exists(manifest):
        txt = open(manifest, encoding="utf-8").read()
        old = json.loads(txt[txt.index("["):txt.rindex("]") + 1])
    games = {g.upper() for g in a.games}
    keep = [t for t in old if t["game"] not in games]
    todo = [t for g in a.games for t in collect[g]()]
    print(f"{len(todo)} tracks to render for {', '.join(sorted(games))}")
    done, failed = [], []
    with ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(render, t, a.force): t for t in todo}
        for i, fu in enumerate(as_completed(futs), 1):
            t = futs[fu]
            try:
                done.append(fu.result())
            except Exception as e:
                failed.append((t["id"], str(e)[:200]))
            if i % 25 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} ({len(failed)} failed)", flush=True)
    for t in done:
        t.pop("parts", None)
    kept = audible(done, a.jobs)
    print(f"{len(done) - len(kept)} silent or stinger-length tracks left out")
    allt = sorted(keep + kept, key=lambda t: (t["game"], t["area"], t["kind"], t["label"]))
    os.makedirs(OUT, exist_ok=True)
    with open(manifest, "w", encoding="utf-8") as fh:
        fh.write("window.TRACKS = " + json.dumps(allt, indent=0) + ";\n")
    hours = sum(t.get("dur", 0) for t in allt) / 3600
    print(f"{len(allt)} tracks in {manifest} ({hours:.1f} h)")
    for tid, err in failed:
        print("FAILED", tid, err)

if __name__ == "__main__":
    main()
