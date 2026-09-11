from __future__ import annotations

import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

import unrealsdk
from mods_base import ButtonOption, CoopSupport, Game, build_mod, keybind
from unrealsdk import hooks

__version__ = "1.0"
__author__ = "alexa"

MOD_ID = "music_watch"
REPORT_PATH = Path(__file__).parent / "music_watch.txt"

# ---------------------------------------------------------------------------
# Why this exists.
#
# We can rewrite BL4's music zone data and verify it 94/94, but we could not
# tell whether the music actually changed. The test was bad: I said CellSize
# 2000 meant "walk 30m to cross a boundary", but CellSize is the PAINT GRID
# RESOLUTION, not the zone size. Neighbouring cells almost always carry the
# same tag, so 30m crosses cell edges without ever crossing a zone. On a map
# this size a real zone transition could be hundreds of metres away, with no
# way to know when you had crossed one.
#
# So stop inferring the triggers from behaviour and just watch them. This hooks
# the audio calls and logs each one as it fires. Walk around normally and the
# log becomes a trace of exactly what drives music, where, and how often.
#
#   (hooks install automatically while the mod is enabled)
#   F2   toggle log_all_calls - logs EVERY unreal call to Plugins/unrealsdk.calls.tsv.
#        Enormous. Use in bursts of a few seconds only.
#   F3   write a summary report of what has fired so far
# ---------------------------------------------------------------------------

# class name -> functions on it worth watching
WATCH: dict[str, tuple[str, ...]] = {
    "GbxAudioBlueprintFunctionLibrary": (
        "SetSwitch",
        "SetState",
        "PostEventInWorld",
        "SetGlobalRtpc",
        "SetManagedLoopSwitch",
    ),
    "GbxGameAudioBlueprintFunctionLibrary": ("PostEventInWorldMulticast",),
}

_counts: Counter[str] = Counter()
_recent: list[str] = []
_installed: list[str] = []
_all_calls_on = False
MAX_RECENT = 400


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _path(obj) -> str:
    return _safe(lambda: obj._path_name(), "?") or "?"


def _describe(args) -> str:
    """Summarise a call's args without assuming which fields exist."""
    bits = []
    for name in ("Switch", "StateName", "Event", "Rtpc", "RtpcValue", "OptionalSwitch"):
        val = _safe(lambda n=name: getattr(args, n))
        if val is not None:
            text = str(_safe(lambda v=val: repr(v), "?"))
            bits.append(f"{name}={text[:150]}")
    if not bits:
        bits.append(str(_safe(lambda: repr(args), "?"))[:200])
    return "  ".join(bits)


def _make_callback(label: str):
    def cb(obj, args, _ret, _func):  # noqa: ANN001
        try:
            _counts[label] += 1
            line = f"{time.strftime('%H:%M:%S')}  {label}  {_describe(args)}"
            _recent.append(line)
            if len(_recent) > MAX_RECENT:
                del _recent[0]
            # Log the first few of each kind, then go quiet so the log stays usable.
            if _counts[label] <= 8:
                unrealsdk.logging.info(f"[{MOD_ID}] {line}")
            elif _counts[label] == 9:
                unrealsdk.logging.info(
                    f"[{MOD_ID}] {label}: further calls counted silently, press F3 for totals",
                )
        except Exception:
            unrealsdk.logging.error(traceback.format_exc())
        return None

    return cb


def _install() -> None:
    _installed.clear()
    for cls_name, funcs in WATCH.items():
        cls = _safe(lambda n=cls_name: unrealsdk.find_class(n))
        if cls is None:
            unrealsdk.logging.info(f"[{MOD_ID}] class not found: {cls_name}")
            continue
        # Resolve the real package path rather than guessing /Script/GbxAudio.
        cls_path = _path(cls)
        for fn in funcs:
            target = f"{cls_path}:{fn}"
            ok = _safe(
                lambda t=target, f=fn: hooks.add_hook(
                    t,
                    hooks.Type.PRE,
                    f"{MOD_ID}_{f}",
                    _make_callback(f),
                ),
                False,
            )
            unrealsdk.logging.info(f"[{MOD_ID}] hook {'OK  ' if ok else 'FAIL'} {target}")
            if ok:
                _installed.append(target)
    unrealsdk.logging.info(f"[{MOD_ID}] {len(_installed)} hook(s) active - go play, then press F3")


def _uninstall() -> None:
    for target in _installed:
        fn = target.rsplit(":", 1)[-1]
        _safe(lambda t=target, f=fn: hooks.remove_hook(t, hooks.Type.PRE, f"{MOD_ID}_{f}"))
    _installed.clear()
    if _all_calls_on:
        _safe(lambda: hooks.log_all_calls(False))


def toggle_all_calls() -> None:
    """F2. The nuclear option - every unreal call, to Plugins/unrealsdk.calls.tsv."""
    global _all_calls_on
    try:
        _all_calls_on = not _all_calls_on
        hooks.log_all_calls(_all_calls_on)
        unrealsdk.logging.info(
            f"[{MOD_ID}] log_all_calls {'ON - TURN IT OFF IN A FEW SECONDS' if _all_calls_on else 'off'}",
        )
    except Exception:
        unrealsdk.logging.error(traceback.format_exc())


def write_report() -> None:
    """F3."""
    try:
        with REPORT_PATH.open("w", encoding="utf-8") as f:
            f.write("# What BL4's audio system actually called\n\n")
            f.write(f"hooks active: {len(_installed)}\n")
            for t in _installed:
                f.write(f"    {t}\n")
            f.write("\n## call counts\n")
            if not _counts:
                f.write(
                    "  NOTHING FIRED.\n"
                    "  Either these are not the functions BL4 uses for music, or music is\n"
                    "  driven from native C++ that never goes through the reflected call\n"
                    "  path. Next step is F2 (log_all_calls) for a few seconds while the\n"
                    "  music changes, then grep the tsv.\n",
                )
            for label, n in _counts.most_common():
                f.write(f"  {n:6d}  {label}\n")
            f.write(f"\n## last {len(_recent)} calls\n")
            for line in _recent:
                f.write(f"  {line}\n")
        unrealsdk.logging.info(f"[{MOD_ID}] wrote {REPORT_PATH}  ({sum(_counts.values())} calls)")
    except Exception:
        unrealsdk.logging.error(traceback.format_exc())


@keybind("Toggle log_all_calls", "F2")
def all_calls_keybind() -> None:
    toggle_all_calls()


@keybind("Write Watch Report", "F3")
def report_keybind() -> None:
    write_report()


all_calls_button = ButtonOption(
    "Toggle log_all_calls",
    description="Logs EVERY unreal call to Plugins/unrealsdk.calls.tsv. Huge - seconds only.",
    on_press=lambda _: toggle_all_calls(),
)

report_button = ButtonOption(
    "Write Watch Report",
    description="Write what the audio hooks have seen so far to music_watch.txt.",
    on_press=lambda _: write_report(),
)


mod = build_mod(
    supported_games=Game.BL4,
    coop_support=CoopSupport.ClientSide,
    on_enable=_install,
    on_disable=_uninstall,
)
