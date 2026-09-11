from __future__ import annotations

import re
import traceback
from pathlib import Path
from typing import Any

import unrealsdk
from mods_base import ButtonOption, CoopSupport, Game, build_mod, keybind

__version__ = "2.2"
__author__ = "alexa"

MOD_ID = "music_remap"
REPORT_PATH = Path(__file__).parent / "music_remap.txt"
# F4 and F5 write DIFFERENT reports. v2.1 pointed both at switch_api.txt, so
# whichever ran second silently destroyed the other's output.
AUDIO_API_PATH = Path(__file__).parent / "audio_api.txt"
SWITCH_API_PATH = Path(__file__).parent / "switch_api.txt"

# ---------------------------------------------------------------------------
# v1.0 result: writing the data WORKS. 94 zone actions rewritten across 5 live
# layers, 0 failures, read-back verified 94/94, restore verified too. The full
# write-back chain is required (entry -> actions -> OnActivation -> zone ->
# MusicZones -> layer) because pyunrealsdk hands out struct COPIES from arrays.
#
# It also corrected a wrong conclusion of mine: BL4 has 91 distinct music
# switch values, not five. The earlier probe truncated each MusicZones[] array
# to its first entry so only the ":default" of each group was visible. The
# music is not missing, it is just barely deployed.
#
# But the audible result was ambiguous - "I think I heard a change". Two
# reasons, both fixed here:
#
#   1. v1 cycled the pool ALPHABETICALLY, which meant stepping through a dozen
#      similar city-ambience cues. F6 now walks a curated list of maximally
#      contrasting cues instead.
#   2. A data edit only takes effect on the next zone activation, so you are
#      comparing music separated by a walk - the worst possible A/B test.
#      F5 hunts for the function that actually pushes the switch to Wwise. If
#      we can call that directly, the test becomes press-key-hear-it, and it is
#      a better architecture for the finished mod anyway.
#
# v2.1: fixes a snapshot bug that cached an empty result when the mod was
# enabled outside a level, and adds F4.
#
#   F4   inspect the direct-call path: SetSwitch needs a PlaybackInstance, and
#        PostEventInWorld returns one, so resolve those struct types and hunt
#        for a music event we could post
#   F5   sweep for a direct "set Wwise switch" call, dump signatures
#   F6   apply the next CONTRASTING switch to every music zone
#   F12  restore the original mapping
# ---------------------------------------------------------------------------

MUSIC_LAYER_CLASS = "OakWorldPainterLayer_MusicZone"

# Deliberately maximally different from each other, so "did it change" needs no
# careful listening. Matched as substrings against the discovered pool; any that
# are missing are skipped.
CONTRAST_PREFERENCES = (
    "mus_biome:shatterlands",
    "mus_biome:city",
    "Sha_CarcadiaBesieged",
    "Mnt_GhostOfSanc3",
    "Gr_CityReveal",
    "CTY:TVSquare",
    "mus_biome:ripperA",
)

# Function-name hints for something that pushes a switch into the sound engine.
SWITCH_HINTS = ("switch", "rtpc", "gameparameter", "wwisestate", "postevent", "setstate")

# layer path -> list of (zone_index, action_index, original switch value)
_originals: dict[str, list[tuple[int, int, Any]]] = {}
_pool: list[tuple[str, Any]] = []
_contrast: list[tuple[str, Any]] = []
_index = -1
_snapshot_taken = False


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


def _label(switch) -> str:
    text = _safe(lambda: repr(switch), "") or ""
    m = re.search(r"FGbxDefPtr\('([^']*)'", text)
    return m.group(1) if m else text[:60]


def _layers() -> list[Any]:
    return [
        o
        for o in (
            _safe(lambda: list(unrealsdk.find_all(MUSIC_LAYER_CLASS, exact=False)), []) or []
        )
        if not _is_cdo(o)
    ]


def _props(struct_obj) -> list[tuple[str, str]]:
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


def _read_switches(layer) -> list[tuple[int, int, Any]]:
    out: list[tuple[int, int, Any]] = []
    zones = _safe(lambda: layer.MusicZones)
    if zones is None:
        return out
    for zi in range(_safe(lambda: len(zones), 0) or 0):
        zone = _safe(lambda: zones[zi])
        if zone is None:
            continue
        acts = _safe(lambda: zone.OnActivation.actions)
        if acts is None:
            continue
        for ai in range(_safe(lambda: len(acts), 0) or 0):
            entry = _safe(lambda: acts[ai])
            if entry is None:
                continue
            sw = _safe(lambda: entry.WwiseSwitch)
            if sw is not None:
                out.append((zi, ai, sw))
    return out


def _write_switch(layer, zone_index: int, action_index: int, new_switch: Any) -> bool:
    """Full write-back chain - v1 proved every level of this is needed."""
    try:
        zones = layer.MusicZones
        zone = zones[zone_index]
        act = zone.OnActivation
        acts = act.actions
        entry = acts[action_index]

        entry.WwiseSwitch = new_switch

        acts[action_index] = entry
        act.actions = acts
        zone.OnActivation = act
        zones[zone_index] = zone
        layer.MusicZones = zones
        return True
    except Exception:
        unrealsdk.logging.error(traceback.format_exc())
        return False


def _take_snapshot() -> None:
    """
    Snapshot originals and rebuild the pool. INCREMENTAL, and deliberately so.

    v2.0 bug: this bailed out on a `_snapshot_taken` flag that was set even when
    zero layers were found. Enabling the mod at the main menu therefore cached an
    empty snapshot forever, and every later keypress reported "no switches found"
    despite the layers having loaded in the meantime.

    Now: nothing is cached until layers actually exist, and each call picks up
    any newly-loaded layer without disturbing ones already snapshotted. That also
    handles level transitions, which the finished mod needs regardless.
    """
    global _snapshot_taken, _pool, _contrast

    layers = _layers()
    if not layers:
        unrealsdk.logging.info(
            f"[{MOD_ID}] no music layers loaded yet - will retry on next press",
        )
        return

    added = 0
    for layer in layers:
        key = _path(layer)
        if key not in _originals:
            _originals[key] = _read_switches(layer)
            added += 1

    # Pool comes from the ORIGINALS, so remapping never pollutes the choices.
    seen: dict[str, Any] = {}
    for triples in _originals.values():
        for _, _, sw in triples:
            lbl = _label(sw)
            if lbl and lbl != "None" and lbl not in seen:
                seen[lbl] = sw
    _pool = sorted(seen.items())

    # Build the contrast list in the declared order, skipping any that are absent.
    _contrast = []
    for want in CONTRAST_PREFERENCES:
        for lbl, val in _pool:
            if want.lower() in lbl.lower():
                _contrast.append((lbl, val))
                break
    if not _contrast:
        _contrast = _pool[:6]

    if added or not _snapshot_taken:
        unrealsdk.logging.info(
            f"[{MOD_ID}] snapshot: {len(_originals)} layer(s) (+{added} new), "
            f"{len(_pool)} switches, {len(_contrast)} contrast picks",
        )
        for lbl, _ in _contrast:
            unrealsdk.logging.info(f"[{MOD_ID}]   contrast: {lbl}")
    _snapshot_taken = True


def apply_next() -> None:
    """F6. Every music zone -> the next maximally-different switch."""
    global _index
    try:
        _take_snapshot()
        if not _contrast:
            unrealsdk.logging.error(f"[{MOD_ID}] no switches found - are music layers loaded?")
            return

        _index = (_index + 1) % len(_contrast)
        label, value = _contrast[_index]

        written = failed = 0
        for layer in _layers():
            for zi, ai, _ in _read_switches(layer):
                if _write_switch(layer, zi, ai, value):
                    written += 1
                else:
                    failed += 1

        stuck = sum(
            1 for layer in _layers() for _, _, sw in _read_switches(layer) if _label(sw) == label
        )
        total = sum(len(_read_switches(layer)) for layer in _layers())
        unrealsdk.logging.info(
            f"[{MOD_ID}] >>> '{label}' ({_index + 1}/{len(_contrast)}) - "
            f"wrote {written}, failed {failed}, verify {stuck}/{total}"
            + ("" if stuck else "  <- WRITE DID NOT STICK"),
        )
    except Exception:
        unrealsdk.logging.error(traceback.format_exc())


def restore() -> None:
    """F12."""
    try:
        if not _originals:
            unrealsdk.logging.info(f"[{MOD_ID}] nothing to restore")
            return
        restored = 0
        for layer in _layers():
            for zi, ai, sw in _originals.get(_path(layer), []):
                if _write_switch(layer, zi, ai, sw):
                    restored += 1
        unrealsdk.logging.info(f"[{MOD_ID}] restored {restored} zone action(s)")
    except Exception:
        unrealsdk.logging.error(traceback.format_exc())


def find_switch_api() -> None:
    """
    F5. Find the call that actually pushes a switch to Wwise.

    Editing the data asset only matters at the next zone activation, which makes
    A/B testing miserable. A direct call would let us hear the change instantly,
    and is what the finished mod should use anyway.
    """
    try:
        classes = _safe(lambda: list(unrealsdk.find_all("Class", exact=False)), []) or []
        with SWITCH_API_PATH.open("w", encoding="utf-8") as f:
            f.write("# Functions that might push a Wwise switch directly\n\n")
            f.write(f"(swept {len(classes)} classes)\n\n")

            hits = 0
            for cls in classes:
                cname = _safe(lambda c=cls: c.Name, "?") or "?"
                for fn in _functions(cls):
                    raw = _safe(lambda x=fn: x.Name, "") or ""
                    if not any(h in raw.lower() for h in SWITCH_HINTS):
                        continue
                    hits += 1
                    if hits <= 250:
                        f.write(f"  [{cname}] {_sig(fn)}\n")
            f.write(f"\n{hits} candidate(s)\n")

            # Anything holding live switch state is worth seeing too.
            f.write("\n\n## live audio-ish singletons\n")
            for name in ("OakAudioGlobals", "GbxAudioGraphManager", "OakAudioUserSettings"):
                objs = [
                    o
                    for o in (
                        _safe(lambda n=name: list(unrealsdk.find_all(n, exact=False)), []) or []
                    )
                    if not _is_cdo(o)
                ]
                f.write(f"\n### {name}: {len(objs)} instance(s)\n")
                for o in objs[:2]:
                    f.write(f"    {_path(o)}\n")

        unrealsdk.logging.info(f"[{MOD_ID}] wrote {SWITCH_API_PATH}")
    except Exception:
        unrealsdk.logging.error(traceback.format_exc())


def _detailed_params(fn) -> str:
    """Like _sig, but resolves StructProperty/GbxDefPtrProperty to their real type."""
    parts = []
    field = _safe(lambda: fn.ChildProperties)
    seen = 0
    while field is not None and seen < 60:
        name = _safe(lambda f=field: f.Name, "?")
        ptype = _safe(lambda f=field: f.Class.Name, "?")
        inner = _safe(lambda f=field: f.Struct.Name)
        parts.append(f"{ptype}<{inner}> {name}" if inner else f"{ptype} {name}")
        field = _safe(lambda f=field: f.Next)
        seen += 1
    return ", ".join(parts)


def inspect_audio_api() -> None:
    """
    F4. Work out how to change music instantly instead of on zone activation.

    SetSwitch(PlaybackInstance, Switch) is the direct call, but it needs a
    PlaybackInstance handle. PostEventInWorld RETURNS one, so the chain could be
    post-a-music-event -> keep the instance -> SetSwitch on it. This resolves the
    real struct types those take, and hunts for a music event we could post.
    Read-only.
    """
    try:
        with AUDIO_API_PATH.open("w", encoding="utf-8") as f:
            f.write("# The direct-call path to changing music\n\n")

            f.write("## GbxAudioBlueprintFunctionLibrary, with struct types resolved\n")
            cls = _safe(lambda: unrealsdk.find_class("GbxAudioBlueprintFunctionLibrary"))
            if cls is None:
                f.write("  CLASS NOT FOUND\n")
            else:
                for fn in _functions(cls):
                    nm = _safe(lambda x=fn: x.Name, "?")
                    f.write(f"  {nm}({_detailed_params(fn)})\n")

            f.write("\n\n## live WwiseEventDef objects mentioning music\n")
            events = [
                o
                for o in (
                    _safe(lambda: list(unrealsdk.find_all("WwiseEventDef", exact=False)), []) or []
                )
                if not _is_cdo(o)
            ]
            f.write(f"{len(events)} live WwiseEventDef(s) total\n")
            musical = [o for o in events if "mus" in (_path(o) or "").lower()]
            f.write(f"{len(musical)} mention music:\n")
            for o in musical[:40]:
                f.write(f"    {_path(o)}\n")
            f.write("\nfirst 25 of all events, for shape:\n")
            for o in events[:25]:
                f.write(f"    {_path(o)}\n")

            f.write("\n\n## live WwiseSwitchDef objects\n")
            switches = [
                o
                for o in (
                    _safe(lambda: list(unrealsdk.find_all("WwiseSwitchDef", exact=False)), [])
                    or []
                )
                if not _is_cdo(o)
            ]
            f.write(f"{len(switches)} live:\n")
            for o in switches[:40]:
                f.write(f"    {_path(o)}\n")

            f.write("\n\n## music layers currently loaded\n")
            for layer in _layers():
                f.write(f"    {_path(layer)}  ({len(_read_switches(layer))} zone actions)\n")

        unrealsdk.logging.info(f"[{MOD_ID}] wrote {AUDIO_API_PATH}")
    except Exception:
        unrealsdk.logging.error(traceback.format_exc())


def dump_state() -> None:
    try:
        _take_snapshot()
        with REPORT_PATH.open("w", encoding="utf-8") as f:
            f.write("# Music zone -> Wwise switch mapping\n\n")
            f.write(f"## full pool ({len(_pool)})\n")
            for i, (lbl, _) in enumerate(_pool):
                f.write(f"  [{i}] {lbl}\n")
            f.write(f"\n## contrast picks ({len(_contrast)})\n")
            for i, (lbl, _) in enumerate(_contrast):
                f.write(f"  [{i}] {lbl}{'  <- active' if i == _index else ''}\n")
            f.write("\n\n## live layers\n")
            for layer in _layers():
                triples = _read_switches(layer)
                f.write(f"\n### {_path(layer)}\n  {len(triples)} zone action(s)\n")
                zones = _safe(lambda: layer.MusicZones)
                for zi, ai, sw in triples:
                    tag = _safe(lambda: repr(zones[zi].Tag), "?")
                    f.write(f"    zone[{zi}].action[{ai}]  tag={tag}  -> {_label(sw)}\n")
        unrealsdk.logging.info(f"[{MOD_ID}] wrote {REPORT_PATH}")
    except Exception:
        unrealsdk.logging.error(traceback.format_exc())


@keybind("Inspect Audio API", "F4")
def inspect_keybind() -> None:
    inspect_audio_api()


@keybind("Find Switch API", "F5")
def api_keybind() -> None:
    find_switch_api()


@keybind("Next Contrasting Music", "F6")
def next_keybind() -> None:
    apply_next()


@keybind("Restore Original Music", "F12")
def restore_keybind() -> None:
    restore()


api_button = ButtonOption(
    "Find Switch API",
    description="Sweep for a function that sets a Wwise switch directly, for instant A/B testing.",
    on_press=lambda _: find_switch_api(),
)

apply_button = ButtonOption(
    "Next Contrasting Music",
    description="Point every music zone at the next deliberately-different cue.",
    on_press=lambda _: apply_next(),
)

restore_button = ButtonOption(
    "Restore Original Music",
    description="Put every zone back the way it was.",
    on_press=lambda _: restore(),
)

dump_button = ButtonOption(
    "Dump Current Mapping",
    description="Write the live zone -> switch mapping to music_remap.txt.",
    on_press=lambda _: dump_state(),
)


def _on_enable() -> None:
    _take_snapshot()


def _on_disable() -> None:
    restore()


mod = build_mod(
    supported_games=Game.BL4,
    coop_support=CoopSupport.ClientSide,
    on_enable=_on_enable,
    on_disable=_on_disable,
)
