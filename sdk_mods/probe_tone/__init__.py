from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

import unrealsdk
from mods_base import ButtonOption, CoopSupport, Game, build_mod, keybind

__version__ = "1.5"
__author__ = "alexa"

MOD_ID = "probe_tone"
REPORT_PATH = Path(__file__).parent / "probe_tone.txt"

# ---------------------------------------------------------------------------
# The experiment everything has been building towards.
#
# ProbeTone_P.{pak,ucas,utoc} in OakGame/Content/Paks contains one asset:
# /Game/ProbeAudio/ProbeTone, a 4-second 440Hz warbling tone, cooked by UE 5.5.4
# as uncompressed PCM (.ubulk is exactly 352,800 bytes = 44100 * 4 * 2) and
# converted with `retoc to-zen --version UE5_5`. Its package header reports
# fileVersionUE5 = 1013, matching BL4 exactly.
#
# If this plays, then: BL4's native UE5 mixer works, custom cooked assets load
# from a modded container, and the whole parallel-audio plan is viable. If it
# does not, the reason will be in this report rather than left to guesswork.
#
# Known from earlier rounds:
#   - unrealsdk.load_package() is DEAD in this build (returns None for
#     everything, even packages provably loaded). Do not use it.
#   - KismetSystemLibrary.LoadAsset_Blocking is NOT callable from python:
#     pyunrealsdk resolves SoftObjectProperty to a UObject, with no path setter.
#   - GameplayStatics.SpawnSound2D(World, Sound, Vol, Pitch, Start,
#     Concurrency, bPersistAcrossLevelTransition, bAutoDestroy) -> AudioComponent
#
#   F1   load and play the tone
#   F11  stop it
# ---------------------------------------------------------------------------

# SoftObjectPaths want Package.ObjectName. Several spellings are tried because
# pyunrealsdk's coercion of a python str into a SoftObjectProperty is the one
# step here with no prior evidence behind it.
HIJACK_PATH = "/Game/GameData/Audio/Music/WorldPaintLayers/WPLayer_Mus_Ambiance_CTY.WPLayer_Mus_Ambiance_CTY"

ASSET_PATHS = (
    "/Game/ProbeAudio/ProbeTone.ProbeTone",
    "/Game/ProbeAudio/ProbeTone",
    "SoundWave'/Game/ProbeAudio/ProbeTone.ProbeTone'",
)

_component: Any = None
_loaded: Any = None


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


def _world():
    for cls in ("OakPlayerController", "PlayerController"):
        objs = [
            o
            for o in (_safe(lambda c=cls: list(unrealsdk.find_all(c, exact=False)), []) or [])
            if not _is_cdo(o)
        ]
        if objs:
            return objs[-1]
    return None


def play_tone() -> None:
    global _component, _loaded
    lines: list[str] = []

    def say(msg: str) -> None:
        lines.append(msg)
        unrealsdk.logging.info(f"[{MOD_ID}] {msg}")

    try:
        say("=== probe tone ===")

        # 0. THE OVERRIDE TEST. This is the decisive one.
        #
        #    ZZHijack_9999_P ships our tone under the package NAME of a BL4 asset
        #    that is already resident (WPLayer_Mus_Ambiance_CTY, the city ambient
        #    music layer). So the container OVERRIDES rather than ADDS.
        #
        #    Hypothesis: _P containers can override existing packages but cannot
        #    add new ones, because adding needs BL4's global package store to
        #    gain an entry at mount time while overriding reuses a chunk id that
        #    already exists. Every working BL4 mod overrides; none adds.
        #
        #    Reading the result:
        #      SoundWave found here  -> conclusive. Container mounts, overrides
        #                               work, adding is the specific failure.
        #      MusicZone still there -> container did not override; either it is
        #                               not mounted or it lost to load order.
        #      neither               -> INCONCLUSIVE, not proof of anything. The
        #                               package may be failing to load because
        #                               its export class no longer matches what
        #                               the referencing asset imports.
        say("--- override test: WPLayer_Mus_Ambiance_CTY ---")
        for cls_name in ("SoundWave", "OakWorldPainterLayer_MusicZone", "Object"):
            obj = _safe(lambda c=cls_name: unrealsdk.find_object(c, HIJACK_PATH))
            if obj is not None:
                actual = _safe(lambda: obj.Class.Name, "?")
                say(f"  found as {cls_name}: actual class = {actual}")
                if actual == "SoundWave":
                    say("  >>> OVERRIDE WORKED - our asset replaced BL4's")
                    _loaded = obj
                else:
                    say("  >>> still BL4's original - override did not take")
                break
        else:
            say("  package not resident at all - INCONCLUSIVE, see notes above")
        say("")

        # 1. Is it already resident? The container may have been mounted and the
        #    asset pulled in without us asking.
        for p in ASSET_PATHS[:2]:
            obj = _safe(lambda x=p: unrealsdk.find_object("SoundWave", x))
            if obj is not None:
                say(f"already resident via find_object: {_path(obj)}")
                _loaded = obj
                break
        else:
            say("not resident yet - loading")

        # 2. load_package. Previously written off because it returned None for
        #    /Game/Maps/WorldLevels/World_P - but that package was ALREADY
        #    resident, so a None return there only proves the binding's return
        #    value is broken, not that the call does nothing. ProbeTone is not
        #    resident, so this is the case that actually tests it: call it, then
        #    look again. If the package appears, load_package works fine and
        #    just lies about its return value.
        #
        #    LoadAsset_Blocking is NOT usable from python: pyunrealsdk resolves
        #    SoftObjectProperty directly to a UObject and offers no setter for
        #    the path, so you would need the object loaded to ask for it to be
        #    loaded. That was my error - I read the signature and never checked
        #    it was callable.
        if _loaded is None:
            before = len(_safe(lambda: list(unrealsdk.find_all("Package", exact=False)), []) or [])
            say(f"packages resident before: {before}")

            ret = _safe(lambda: unrealsdk.load_package("/Game/ProbeAudio/ProbeTone"), "<raised>")
            say(f"load_package(...) returned {ret!r}  (return value is known unreliable)")

            after = len(_safe(lambda: list(unrealsdk.find_all("Package", exact=False)), []) or [])
            say(f"packages resident after:  {after}   delta={after - before}")

            for p in ASSET_PATHS[:2]:
                obj = _safe(lambda x=p: unrealsdk.find_object("SoundWave", x))
                if obj is not None:
                    say(f"RESOLVED after load_package: {_path(obj)}")
                    _loaded = obj
                    break

        # 3. Did the container mount at all? If no package under /Game/ProbeAudio
        #    exists even after the load attempt, the pak is not being read and no
        #    load route could ever work.
        if _loaded is None:
            hits = [
                _path(p)
                for p in (_safe(lambda: list(unrealsdk.find_all("Package", exact=False)), []) or [])
                if "ProbeAudio" in (_path(p) or "")
            ]
            say(f"packages matching ProbeAudio: {hits if hits else 'NONE - container likely not mounted'}")

        # 3a. THE LEVEL ROUTE.
        #     Both direct load routes are dead: LoadAsset_Blocking cannot be
        #     called (SoftObjectProperty has no path setter in pyunrealsdk) and
        #     load_package is a no-op, proven by the control below failing on
        #     BL4's OWN packages. What is left is Unreal's dependency graph.
        #
        #     The container now also ships /Game/ProbeAudio/ProbeMap, a level
        #     holding an AmbientSound that hard-references ProbeTone (with
        #     auto_activate off, so the level cannot play the tone by itself -
        #     playback must still come from our SpawnSound2D call, or a positive
        #     result would prove nothing about the mixer).
        #
        #     LoadLevelInstance takes a plain StrProperty and is not latent, so
        #     it is callable. Loading the map should drag the SoundWave in.
        if _loaded is None:
            say("")
            say("--- level route: LoadLevelInstance ---")
            lsd = _safe(lambda: unrealsdk.find_class("LevelStreamingDynamic"))
            if lsd is None:
                say("LevelStreamingDynamic not found")
            else:
                world = _world()
                zero_loc = _safe(lambda: unrealsdk.make_struct("Vector", X=0.0, Y=0.0, Z=0.0))
                zero_rot = _safe(lambda: unrealsdk.make_struct("Rotator", Pitch=0.0, Yaw=0.0, Roll=0.0))
                say(f"world={_path(world) if world else 'NONE'} vec={'ok' if zero_loc else 'FAILED'}")
                try:
                    result = lsd.ClassDefaultObject.LoadLevelInstance(
                        world,
                        "/Game/ProbeAudio/ProbeMap",
                        zero_loc,
                        zero_rot,
                        False,
                        "",
                        None,
                        False,
                    )
                    say(f"LoadLevelInstance -> {result}")
                except Exception as e:
                    say(f"LoadLevelInstance raised {type(e).__name__}: {e}")

                # Did the sound come along for the ride?
                for p in ASSET_PATHS[:2]:
                    obj = _safe(lambda x=p: unrealsdk.find_object("SoundWave", x))
                    if obj is not None:
                        say(f"SOUND RESOLVED via level dependency: {_path(obj)}")
                        _loaded = obj
                        break
                if _loaded is None:
                    hits = [
                        _path(p)
                        for p in (
                            _safe(lambda: list(unrealsdk.find_all("Package", exact=False)), []) or []
                        )
                        if "ProbeAudio" in (_path(p) or "")
                    ]
                    say(f"packages matching ProbeAudio now: {hits if hits else 'still NONE'}")

        # 3b. CONTROL. "Our asset did not load" is ambiguous between
        #     load_package being broken and our container not being mounted.
        #     These are BL4's OWN packages, known to exist in its paks and
        #     reported absent-from-memory by the earlier music probe. If they
        #     load, load_package works and the fault is our container. If they
        #     do not, load_package is simply broken and our container's status
        #     is still unknown.
        if _loaded is None:
            say("")
            say("--- control: load BL4's own non-resident packages ---")
            controls = (
                "/Game/GameData/Audio/Music/Moments/Moment_Mus_Intro_100_FindTheStash",
                "/Game/GameData/Audio/Music/WorldPaintLayers/WPLayer_Mus_Vault",
                "/Game/GameData/Audio/FoleyImpactData/FolImp_Jump",
            )
            any_control_loaded = False
            for cp in controls:
                pre = _safe(lambda: unrealsdk.find_object("Package", cp)) is not None
                _safe(lambda x=cp: unrealsdk.load_package(x))
                post = _safe(lambda: unrealsdk.find_object("Package", cp)) is not None
                say(f"  {'resident' if pre else 'absent  '} -> {'RESIDENT' if post else 'still absent'}   {cp}")
                if post and not pre:
                    any_control_loaded = True
            say("")
            if any_control_loaded:
                say("VERDICT: load_package WORKS on BL4's own packages.")
                say("  -> the fault is our container: it is not being mounted.")
            else:
                say("VERDICT: load_package does nothing even for BL4's own packages.")
                say("  -> load_package is broken in this SDK build; our container's")
                say("     mount status remains untested. Need a different load")
                say("     trigger (e.g. a cooked map loaded via a Name/Str API).")

        if _loaded is None:
            say("FAIL: could not load the asset by any route")
            return

        # 4. What did we actually get?
        say(f"class    = {_safe(lambda: _loaded.Class.Name, '?')}")
        say(f"duration = {_safe(lambda: _loaded.Duration, '?')}")
        say(f"channels = {_safe(lambda: _loaded.NumChannels, '?')}")
        say(f"rate     = {_safe(lambda: _loaded.SampleRate, '?')}")
        say(f"codec    = {_safe(lambda: _loaded.SoundAssetCompressionType, '?')}")

        # 5. Play it. This is the moment of truth for the native mixer.
        world = _world()
        say(f"world    = {_path(world) if world else 'NONE'}")
        gs = unrealsdk.find_class("GameplayStatics").ClassDefaultObject

        # Is there an audio device at all? SpawnSound2D returns nullptr when
        # there is no usable one, which is the leading explanation for the None
        # we got with a valid, fully-loaded SoundWave. These read the device
        # rather than guessing from a failed call.
        say("--- audio device ---")
        say(f"  GetMaxAudioChannelCount = {_safe(lambda: gs.GetMaxAudioChannelCount(world), '<raised>')}")
        say(f"  GetAudioTimeSeconds     = {_safe(lambda: gs.GetAudioTimeSeconds(world), '<raised>')}")
        say(f"  GetRealTimeSeconds      = {_safe(lambda: gs.GetRealTimeSeconds(world), '<raised>')}")
        say(f"  AreSubtitlesEnabled     = {_safe(lambda: gs.AreSubtitlesEnabled(), '<raised>')}")
        listeners = _safe(
            lambda: gs.AreAnyListenersWithinRange(
                world, unrealsdk.make_struct("Vector", X=0.0, Y=0.0, Z=0.0), 1.0e9
            ),
            "<raised>",
        )
        say(f"  AreAnyListenersWithinRange = {listeners}   (False with a huge radius means no listener)")

        # CreateSound2D allocates a component WITHOUT playing. If even this
        # returns None the device is the problem, not playback.
        created = _safe(lambda: gs.CreateSound2D(world, _loaded, 1.0, 1.0, 0.0, None, True, True))
        say(f"  CreateSound2D -> {created}")
        if created is not None:
            say("  component allocated; calling Play() on it directly")
            _safe(lambda: created.Play(0.0))
            _component = created

        say("--- playback attempts ---")
        spawned = _safe(lambda: gs.SpawnSound2D(world, _loaded, 1.0, 1.0, 0.0, None, True, True))
        say(f"  SpawnSound2D -> {spawned}")
        if spawned is not None:
            _component = spawned

        _safe(lambda: gs.PlaySound2D(world, _loaded, 1.0, 1.0, 0.0, None, None, True))
        say("  PlaySound2D returned")

        # Did anything actually get created anywhere in the engine?
        live = [
            o
            for o in (
                _safe(lambda: list(unrealsdk.find_all("AudioComponent", exact=False)), []) or []
            )
            if not _is_cdo(o)
        ]
        say(f"  live AudioComponents now: {len(live)}")
        for o in live[:5]:
            say(f"    {_path(o)}")

        if _component is None and not live:
            say("")
            say("VERDICT: no component could be created from a valid SoundWave.")
            say("  -> BL4 almost certainly never initialises UE's native mixer,")
            say("     because it runs Wwise instead. Check the channel count above.")
        elif _component is not None:
            say("")
            say("LISTEN NOW - 4 seconds of 440Hz warble")
    except Exception:
        unrealsdk.logging.error(traceback.format_exc())
        lines.append(traceback.format_exc())
    finally:
        try:
            REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
        except Exception:
            pass


def stop_tone() -> None:
    global _component
    try:
        if _component is not None:
            _component.Stop()
            unrealsdk.logging.info(f"[{MOD_ID}] stopped")
            _component = None
    except Exception:
        unrealsdk.logging.error(traceback.format_exc())


@keybind("Play Probe Tone", "F1")
def play_keybind() -> None:
    play_tone()


@keybind("Stop Probe Tone", "F11")
def stop_keybind() -> None:
    stop_tone()


play_button = ButtonOption(
    "Play Probe Tone",
    description="Load /Game/ProbeAudio/ProbeTone from the modded container and play it.",
    on_press=lambda _: play_tone(),
)

stop_button = ButtonOption(
    "Stop Probe Tone",
    description="Stop it.",
    on_press=lambda _: stop_tone(),
)


mod = build_mod(
    supported_games=Game.BL4,
    coop_support=CoopSupport.ClientSide,
)
