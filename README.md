# Borderlands 4 Music Patch

Work in progress toward giving BL4 the ambient and combat music it lacks.

**The original approach does not work, and now we know exactly why.** BL4 never
initialises Unreal's native audio mixer — it runs Wwise exclusively. Custom
`USoundWave` assets cook, pack, mount and load perfectly, and then cannot be played,
because there is no audio device underneath the API. Measured: zero audio channels,
no listener, and `CreateSound2D` fails to allocate even without playing.

The remaining route is replacing audio inside BL4's own Wwise banks.

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
`retoc to-zen`, drop it in `Paks/`, and the game loads it — verified by reading the
live object's class, duration and sample rate back out of memory. Playing it is what
cannot be done.

Mod containers can also only *override* packages BL4 already ships; they cannot add
new ones. That was tested twice, because the first test confounded the package name
with the mod filename convention.

See `CLAUDE.md` for the technical detail, including several pitfalls that cost
hours — the Zen store silently swallowing cooked output, UE 5.8 producing packages
retoc cannot read, and an in-flight test whose result is confounded by a filename
convention.
