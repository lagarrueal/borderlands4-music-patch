# Borderlands 4 Music Patch

Puts Borderlands 2 and 3 music into Borderlands 4's combat and exploration
music, and stops combat music from dropping out mid-fight.

**Status (2026-10-04): working in game.** The Fadefields, every ripper area and
the Vaults play the picked BL2/BL3 combat tracks. Exploration music across the
Fadefields and Terminus Range, and the combat drop-out fix, are built but not yet
confirmed in game.

## How it works

BL4 plays all of its audio through Wwise. Its music `.wem` files are loose files
in the legacy paks, so a mod pak can replace them and the master music bank
itself. Everything is worked out and checked offline, before the game is
launched:

- **Listen first.** `wwise/listen.py` renders every music cue of BL2, BL3 and
  BL4 to MP3: wwiser rebuilds each cue the way Wwise sequences it, and vgmstream
  plays it. That is 558 tracks. `listen/index.html`, served by
  `listen/serve.py`, is the picker.
- **Build.** `wwise/musicmod.py mods/blmix.json --install`:
  - patches the master bank `1731745708.bnk` in place, field by field;
  - encodes the picks as Wwise Opus (`wwise/wemopus.py`);
  - matches each slot's loudness to the music it replaces;
  - checks that no other music path changed;
  - packs the mod.
- **Game data.** `wwise/musicncs.py` lowers the threat at which combat music
  ends. That threshold is in BL4's `gbxmusic4.ncs`.
- **Review.** `wwise/modpage.py` adds Before, After and Source buttons for
  every change to the picker page.

Nothing from the games is in this repo. Banks, audio, renders and built paks are
generated locally and ignored. Rebuild the mod after any BL4 update that touches
audio or `gbxmusic4.ncs`.

Tools: wwiser, vgmstream (FModel's copy), ffmpeg, repak, retoc, and the NCS
toolkit of the parent `bl4-mods` folder.

`CLAUDE.md` has the technical detail, including the two rules offline renders
cannot show:
- every streamed track declares how many bytes of its file the game preloads,
  and that must cover the file's header;
- BL4's pre-combat build-up playlist is muted by threat curves.

## What is here

- `wwise/` — bank, pak and NCS tooling, the listening library, the mod builder
- `listen/` — the picker page and its local server
- `mods/` — mod specs: which track goes in which slot
- `sdk_mods/` — four Oak2 Python SDK probes used to map BL4's audio system
- `cook/` — scripts to build a test `USoundWave` and pack it into an IoStore container
- `findings/` — raw probe output from in-game runs

## History: the route that does not work

The original approach was to play our own `USoundWave` assets through Unreal.
**BL4 never initialises Unreal's native audio mixer**; it runs Wwise
exclusively. Custom `USoundWave` assets cook, pack, mount and load perfectly,
and then cannot be played, because there is no audio device underneath the API.
Measured: zero audio channels, no listener, and `CreateSound2D` fails to
allocate even without playing.

BL4's music is data-driven but native. Painted world cells carry gameplay tags,
and activating one sets a Wwise switch. There are **91 music cues** in the banks
across five switch groups. The game simply plays very few of them, which is why
it feels musically empty.

The music system runs entirely in C++ and never crosses Unreal's reflected call
boundary, so it cannot be hooked or observed from the SDK. That was confirmed by
capturing 1.1 million reflected calls during play without a single music call
among them.

The full custom-asset pipeline works: cook a `USoundWave` in UE 5.5, convert
with `retoc to-zen`, drop it in `Paks/`, and the game loads it. That was verified
by reading the live object's class, duration and sample rate back out of memory.
Playing it is what cannot be done.

Mod containers can also only *override* packages BL4 already ships; they cannot
add new ones. That was tested twice, because the first test confounded the
package name with the mod filename convention.

`CLAUDE.md` also covers several pitfalls that cost hours:
- the Zen store silently swallowing cooked output;
- UE 5.8 producing packages retoc cannot read;
- an in-flight test whose result was confounded by a filename convention.
