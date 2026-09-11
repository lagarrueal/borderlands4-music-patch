# Borderlands 4 Music Patch

Work in progress toward giving BL4 the ambient and combat music it lacks.

**Not shipping yet.** The investigation is well advanced and one question remains
open: whether BL4 initialises Unreal's native audio mixer at all, or runs Wwise
exclusively. If it does not, custom audio cannot be played alongside the game's own
and the approach has to change.

## What is here

- `sdk_mods/` — four Oak2 Python SDK probes used to map BL4's audio system
- `cook/` — scripts to build a test `USoundWave` and pack it into an IoStore container
- `findings/` — raw probe output from in-game runs

## What was learned

BL4's music is data-driven but native: painted world cells carry gameplay tags, and
activating one sets a Wwise switch. There are **91 music cues** in the banks across
five switch groups — the game simply plays very few of them, which is why it feels
musically empty. All audio lives inside the Wwise banks and is addressable only by
switch.

The music system runs entirely in C++ and never crosses Unreal's reflected call
boundary, so it cannot be hooked or observed from the SDK — confirmed by capturing
1.1 million reflected calls during play without a single music call among them.

The full custom-asset pipeline works: cook a `USoundWave` in UE 5.5, convert with
`retoc to-zen`, drop it in `Paks/`, and the game loads it. Playing it is the part
that does not yet work.

See `CLAUDE.md` for the technical detail, including several pitfalls that cost
hours — the Zen store silently swallowing cooked output, UE 5.8 producing packages
retoc cannot read, and an in-flight test whose result is confounded by a filename
convention.
