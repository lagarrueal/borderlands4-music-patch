from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

import unrealsdk
from mods_base import ButtonOption, CoopSupport, Game, build_mod, keybind

__version__ = "4.0"
__author__ = "alexa"

MOD_ID = "audio_probe"
MUSIC_REPORT = Path(__file__).parent / "music_system.txt"
LOAD_REPORT = Path(__file__).parent / "load_routes.txt"

# ---------------------------------------------------------------------------
# Where we are:
#   - find_object() works. load_package() returns None for everything, even a
#     package we can prove is loaded: BL4 is IoStore-only and legacy sync
#     loading is stubbed. We need another way to pull an asset on demand.
#   - retoc manifests across all 197 containers (99,293 packages) show BL4's
#     ENTIRE music system is 15 assets under /Game/GameData/Audio/Music/.
#     Four biome ambiances, some POIs, and exactly ONE combat layer which is
#     intro-scoped. That is why the game sounds empty.
#
# F8  Dump the music system. The player is in World_P, so several of these
#     layers may already be resident - if so we can read how a music zone is
#     configured without solving the loading problem at all. Read-only.
#
# F9  Find a load route. Sweeps every UClass for functions whose name looks
#     like loading, dumps their real signatures, then tries the promising ones
#     against a music asset. Read-only apart from the load attempts themselves.
# ---------------------------------------------------------------------------

MUSIC_PACKAGES = (
    "/Game/GameData/Audio/Music/WorldPaintLayers/WPLayer_Mus_Ambiance_CTY",
    "/Game/GameData/Audio/Music/WorldPaintLayers/WPLayer_Mus_Ambiance_GR",
    "/Game/GameData/Audio/Music/WorldPaintLayers/WPLayer_Mus_Ambiance_MNT",
    "/Game/GameData/Audio/Music/WorldPaintLayers/WPLayer_Mus_Ambiance_SH",
    "/Game/GameData/Audio/Music/WorldPaintLayers/WPLayer_Mus_ElpisP",
    "/Game/GameData/Audio/Music/WorldPaintLayers/WPLayer_Mus_Fortress_GL_POI",
    "/Game/GameData/Audio/Music/WorldPaintLayers/WPLayer_Mus_Fortress_Mnt_POI",
    "/Game/GameData/Audio/Music/WorldPaintLayers/WPLayer_Mus_Fortress_SL_POI",
    "/Game/GameData/Audio/Music/WorldPaintLayers/WPLayer_Mus_Fortress_UpperCity_POI",
    "/Game/GameData/Audio/Music/WorldPaintLayers/WPLayer_Mus_Intro_Ambiance",
    "/Game/GameData/Audio/Music/WorldPaintLayers/WPLayer_Mus_Intro_Combat",
    "/Game/GameData/Audio/Music/WorldPaintLayers/WPLayer_Mus_Vault",
    "/Game/GameData/Audio/Music/WorldPaintLayers/WPLayer_Mus_WorldPZones",
    "/Game/GameData/Audio/Music/Moments/Moment_Mus_Intro_100_FindTheStash",
    "/Game/GameData/Audio/Music/Moments/Moment_Mus_Intro_110_PossessedPrisoner",
    # non-music ambience, for comparison
    "/Game/GameData/Audio/Design/WorldPainterLayers/SymphonicAmbient/World_P/WPLayer_Amb_WorldP_Biome_Grasslands",
    "/Game/GameData/Audio/Design/WorldPainterLayers/Radio/WPLayer_Radio",
    # DLC music layers, from the asset registry. MusLayer_CowbellCombat matters:
    # it proves a combat music layer exists outside the intro, so it is the
    # template to copy when adding our own.
    "/Game/DLC/Cowbell/GameData/Audio/Music/MusLayer_CowbellCombat",
    "/Game/DLC/Cowbell/GameData/Audio/Music/MusLayer_CowbellAmbient",
    "/Game/DLC/Cello/GameData/Audio/MusicLayers/MusLayer_CelloAmbiance",
    "/Game/DLC/Banjo/GameData/Audio/Music/WPL_BanjoAmbientMusic",
)

# The first five are CONFIRMED from the shipped AssetRegistry.bin - these are
# the real classes BL4's music system is built from. The rest are guesses from
# the exe's reflection strings and may not exist.
OAK_AUDIO_CLASSES = (
    "OakWorldPainterLayer_MusicZone",
    "OakWorldPainterLayer_AmbientAudio",
    "OakAudioGlobals",
    "OakAudioUserSettings",
    "OakAudioDamageFeedback",
    "OakMusicDef",
    "OakMusicZone",
    "OakMusicParams",
    "OakWwiseSwitchZone",
    "WwiseEventDef",
    "WwiseSwitchDef",
    "WwiseStateDef",
    "WwiseSoundBankDef",
    "GbxAudioGraphManager",
    "WorldPainterLayer",
)

# What a load-ish function name looks like.
LOAD_HINTS = ("load", "stream", "request", "acquire", "mount", "resolve")

# The asset we actually want. Proving a load route and getting a music layer
# into memory are the same experiment.
LOAD_TARGET = "/Game/GameData/Audio/Music/WorldPaintLayers/WPLayer_Mus_Ambiance_GR"

MAX_VALUE = 400
MAX_DUMP = 30


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _path(obj) -> str:
    return _safe(lambda: obj._path_name(), "?") or "?"


def _is_cdo(obj) -> bool:
    p = _path(obj)
    return p.startswith("/Script/") or "Default__" in p


def _props(struct_obj) -> list[tuple[str, str]]:
    """Walk the FField ChildProperties chain. UE5 keeps params/props here."""
    out = []
    field = _safe(lambda: struct_obj.ChildProperties)
    seen = 0
    while field is not None and seen < 200:
        out.append(
            (
                _safe(lambda f=field: f.Name, "?") or "?",
                _safe(lambda f=field: f.Class.Name, "?") or "?",
            ),
        )
        field = _safe(lambda f=field: f.Next)
        seen += 1
    return out


def _functions(cls) -> list[Any]:
    out = []
    child = _safe(lambda: cls.Children)
    seen = 0
    while child is not None and seen < 2000:
        if _safe(lambda c=child: c.Class.Name) == "Function":
            out.append(child)
        child = _safe(lambda c=child: c.Next)
        seen += 1
    return out


def _sig(fn) -> str:
    return "%s(%s)" % (
        _safe(lambda: fn.Name, "?"),
        ", ".join(f"{t} {n}" for n, t in _props(fn)),
    )


def _dump_object(f, obj) -> None:
    cls = _safe(lambda: obj.Class)
    if cls is None:
        f.write("      <no class>\n")
        return
    # Include inherited properties by walking the superclass chain.
    seen_names = set()
    while cls is not None:
        for name, ptype in _props(cls):
            if name in seen_names:
                continue
            seen_names.add(name)
            val = _safe(lambda o=obj, n=name: repr(getattr(o, n)), "<unreadable>")
            f.write(f"      {ptype:20s} {name} = {str(val)[:MAX_VALUE]}\n")
        cls = _safe(lambda c=cls: c.SuperField)


def _objects_in(pkg) -> list[Any]:
    out = []
    for obj in _safe(lambda: list(unrealsdk.find_all("Object", exact=False)), []) or []:
        if _safe(lambda o=obj: o.Outer) == pkg:
            out.append(obj)
    return out


def dump_music_system() -> None:
    """F8. Read-only."""
    try:
        with MUSIC_REPORT.open("w", encoding="utf-8") as f:
            f.write("# BL4 music system\n\n")

            f.write("## 1. which music packages are resident right now\n")
            resident = []
            for path in MUSIC_PACKAGES:
                try:
                    pkg = unrealsdk.find_object("Package", path)
                    resident.append((path, pkg))
                    f.write(f"  LOADED   {path}\n")
                except Exception:
                    f.write(f"  absent   {path}\n")

            f.write(f"\n{len(resident)} of {len(MUSIC_PACKAGES)} resident\n")

            f.write("\n\n## 2. contents of the resident ones\n")
            for path, pkg in resident[:MAX_DUMP]:
                f.write(f"\n### {path}\n")
                inner = _objects_in(pkg)
                f.write(f"  {len(inner)} object(s)\n")
                for obj in inner[:8]:
                    f.write(f"\n  [{_safe(lambda o=obj: o.Class.Name, '?')}] {_path(obj)}\n")
                    _dump_object(f, obj)

            f.write("\n\n## 3. Gearbox audio classes: do they exist, any instances?\n")
            for name in OAK_AUDIO_CLASSES:
                cls = _safe(lambda n=name: unrealsdk.find_class(n))
                if cls is None:
                    f.write(f"\n### {name}: NOT FOUND\n")
                    continue
                insts = [
                    o
                    for o in (
                        _safe(lambda n=name: list(unrealsdk.find_all(n, exact=False)), []) or []
                    )
                    if not _is_cdo(o)
                ]
                f.write(f"\n### {name}: {len(insts)} live instance(s)\n")
                f.write("  properties:\n")
                for pname, ptype in _props(cls)[:40]:
                    f.write(f"    {ptype:20s} {pname}\n")
                for obj in insts[:3]:
                    f.write(f"\n  instance {_path(obj)}\n")
                    _dump_object(f, obj)

        unrealsdk.logging.info(f"[{MOD_ID}] wrote {MUSIC_REPORT}")
    except Exception:
        unrealsdk.logging.error(traceback.format_exc())


def find_load_routes() -> None:
    """F9. Sweeps for a way to load an asset, then tries it on a music layer."""
    try:
        with LOAD_REPORT.open("w", encoding="utf-8") as f:
            f.write("# Ways to load an asset on demand\n\n")
            f.write(f"target: {LOAD_TARGET}\n\n")

            # --- 1. every load-ish function in the whole class graph -------
            f.write("## 1. candidate functions across all classes\n")
            classes = _safe(lambda: list(unrealsdk.find_all("Class", exact=False)), []) or []
            f.write(f"(swept {len(classes)} classes)\n\n")
            hits = 0
            for cls in classes:
                cname = _safe(lambda c=cls: c.Name, "?") or "?"
                for fn in _functions(cls):
                    fname = (_safe(lambda x=fn: x.Name, "") or "").lower()
                    if not any(h in fname for h in LOAD_HINTS):
                        continue
                    params = _props(fn)
                    # Only interested in things that take a path/object-ish arg.
                    types = {t for _, t in params}
                    if not types & {
                        "SoftObjectProperty",
                        "SoftClassProperty",
                        "StrProperty",
                        "NameProperty",
                        "ObjectProperty",
                        "ClassProperty",
                    }:
                        continue
                    hits += 1
                    if hits <= 200:
                        f.write(f"  [{cname}] {_sig(fn)}\n")
            f.write(f"\n{hits} candidate(s) total\n")

            # --- 2. try the obvious ones ----------------------------------
            f.write("\n\n## 2. load attempts\n")

            f.write("\n### unrealsdk.load_package (known broken, re-checked)\n")
            try:
                f.write(f"    -> {unrealsdk.load_package(LOAD_TARGET)}\n")
            except Exception as e:
                f.write(f"    -> {type(e).__name__}: {e}\n")

            ksl = _safe(lambda: unrealsdk.find_class("KismetSystemLibrary"))
            if ksl is None:
                f.write("\n### KismetSystemLibrary: NOT FOUND\n")
            else:
                f.write("\n### KismetSystemLibrary load functions\n")
                for fn in _functions(ksl):
                    fname = (_safe(lambda x=fn: x.Name, "") or "").lower()
                    if any(h in fname for h in LOAD_HINTS):
                        f.write(f"    {_sig(fn)}\n")

            f.write("\n### did anything become resident?\n")
            try:
                pkg = unrealsdk.find_object("Package", LOAD_TARGET)
                f.write(f"    find_object -> {pkg}\n")
                for obj in _objects_in(pkg)[:8]:
                    f.write(f"      [{_safe(lambda o=obj: o.Class.Name, '?')}] {_path(obj)}\n")
                    _dump_object(f, obj)
            except Exception as e:
                f.write(f"    find_object -> {type(e).__name__}: {e}\n")

        unrealsdk.logging.info(f"[{MOD_ID}] wrote {LOAD_REPORT}")
    except Exception:
        unrealsdk.logging.error(traceback.format_exc())


@keybind("Dump Music System", "F8")
def music_keybind() -> None:
    dump_music_system()


@keybind("Find Load Routes", "F9")
def load_keybind() -> None:
    find_load_routes()


music_button = ButtonOption(
    "Dump Music System",
    description=(
        "Read-only. Reports which of BL4's 15 music layers are resident and dumps"
        " their full configuration, plus any Gearbox audio classes."
    ),
    on_press=lambda _: dump_music_system(),
)

load_button = ButtonOption(
    "Find Load Routes",
    description=(
        "Sweeps every class for functions that look like they load an asset, dumps"
        " their signatures, then tries them against a music layer."
    ),
    on_press=lambda _: find_load_routes(),
)


mod = build_mod(
    supported_games=Game.BL4,
    coop_support=CoopSupport.ClientSide,
)
