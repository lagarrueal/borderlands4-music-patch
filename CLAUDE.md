# BL4 Music Patch

Goal: give BL4 the ambient and combat music it lacks, ideally by importing BL2/BL3
tracks.

**Status: the original plan is dead.** BL4 does not initialise Unreal's native
audio mixer, so custom `USoundWave`s cannot be played alongside the game's audio —
however cleanly they cook, pack and load. The only remaining in-engine route is
overriding BL4's own Wwise bank media. See *Answered: the mixer is off*.

> Packaging rules, `.ncs` handling and safety live in the parent `../CLAUDE.md`.
> This file covers only audio.

## How BL4 chooses music

`OakWorldPainterLayer_MusicZone` holds `MusicZones[]`. Each entry is
`{Tag, Guid, OnActivation{Conditions, actions[]}, Color}`, and the action is a
`WwiseSwitch: FGbxDefPtr('Music.mus_biome:default', 'WwiseSwitchDef')`. The layer
carries `CellSize` (2000 for music, 750 for interior ambience — this is the *paint
grid resolution*, not the zone size), `bMutuallyExclusive`, and `options[]` listing
the paintable tags.

So: a painted world cell carries a gameplay tag → activating it sets a Wwise switch
→ the bank plays whatever sits behind that switch. **All audio lives in the Wwise
banks and is addressable only by switch.** Adding new music to Wwise needs the
Wwise source project, so it is out of reach.

Fifteen layer assets under `/Game/GameData/Audio/Music/`, holding **91 distinct
switch values** across five groups:

| group | values |
|---|---|
| `Music.mus_ambiance_zone_MNT` | 26 |
| `Music.mus_ambience_zone_GR` | 22 |
| `Music.mus_ambiance_zone_SH` | 17 |
| `Music.mus_ambiance_zone_CTY` | 14 |
| `Music.mus_biome` | 12 |

Named per-POI: `CTY:ZadrasLab`, `MNT:Mnt_GhostOfSanc3`, `SH:Sha_CarcadiaBesieged`,
`GR:Gr_MurderMystery`. `mus_biome` carries faction variants (`orderA/B/C`,
`ripperA/B/C/D`).

**Watch the spelling**: Grasslands is `ambi*e*nce`, the other three are
`ambi*a*nce`. Anything matching by string must handle both.

**The music is not missing, it is barely deployed.** 91 cues exist; the game plays
a handful. That makes a remap mod worth building on its own merits, separate from
importing anything.

## What works

**Editing the zone data at runtime.** `sdk_mods/music_remap/` rewrites
`MusicZones[].OnActivation.actions[].WwiseSwitch` across all live layers: 94 zone
actions written, 0 failures, read-back verified 94/94, restore likewise. The full
write-back chain is required — pyunrealsdk hands out struct *copies* from arrays:

```
entry.WwiseSwitch = x  ->  acts[i] = entry  ->  act.actions = acts
                       ->  zone.OnActivation = act  ->  zones[zi] = zone
                       ->  layer.MusicZones = zones
```

Whether the game *acts* on the edit is untested — see below.

**The whole cook → pack → mount → load chain.** Proven end to end with a test tone:

```
UE 5.5.4 cook  ->  fileVersionUE5 = 1013 (exact BL4 match), .ubulk = 352,800 B raw PCM
retoc to-zen   ->  container matching working mods field for field
mount          ->  loaded in game, find_object reports class = SoundWave,
                   duration 4.0, 1ch, 44100Hz, codec 2 (PCM)
```

## What does not work

**BL4's music system cannot be hooked.** `hooks.log_all_calls` captured 1,133,883
reflected calls during real play; not one was a music call. All six audio hooks
installed correctly (`GbxAudioBlueprintFunctionLibrary:{SetSwitch,SetState,
PostEventInWorld,SetGlobalRtpc,SetManagedLoopSwitch}`,
`GbxGameAudioBlueprintFunctionLibrary:PostEventInWorldMulticast`) and none ever
fired. The music system is native C++ and never dispatches through `ProcessEvent`.
Do not spend time trying to instrument it.

**Loading an asset by path is not possible from the SDK.**
`KismetSystemLibrary.LoadAsset_Blocking` is *not callable* — pyunrealsdk resolves
`SoftObjectProperty` directly to a `UObject` and exposes only
`ZSoftObjectProperty._get_identifier_from`, a getter. Passing a string raises
`Unable to cast Python instance of type <class 'str'> to C++ type
'unrealsdk::unreal::UObject'`. You would need the asset loaded to ask for it to be
loaded. The same applies to `GbxLoadAsset_Blocking`, `LoadClassAsset_Blocking` and
`BP_GuardAndLoadAsset`. **None of the 148 load-ish functions takes a plain string
package path.**

**`unrealsdk.load_package` is a no-op** in SDK v3.2.0. Proven properly: called on
three of BL4's *own* non-resident packages, all stayed absent, package count delta
0. (An earlier test on an already-resident package proved nothing and should not
have been trusted.)

**`SpawnSound2D` returns None** even given a valid, fully-loaded `SoundWave`.
`PlaySound2D` returns without raising and produces nothing.

## Answered: the mixer is off

**BL4 does not initialise UE's native audio mixer.** Measured directly.

UE5's AudioMixer is compiled in — XAudio2 and WASAPI backends, the full submix
effect chain, `xaudio2_9redist.dll` on disk — and the playback API is fully
reflected (`AudioComponent` has `SetSound`/`Play`/`FadeIn`/`FadeOut`/`PlayQuantized`;
`GameplayStatics` has 141 functions). But nothing has ever made a sound.

With a valid, fully-loaded `SoundWave` in hand (class=SoundWave, duration 4.0, 1ch,
44100Hz, codec 2 = PCM):

```
GetMaxAudioChannelCount    = 0
AreAnyListenersWithinRange = False   (with a 1e9 radius - no listener exists)
CreateSound2D              -> None   (allocates WITHOUT playing; still fails)
SpawnSound2D               -> None
live AudioComponents        = 0
```

Zero channels, no listener, and not even a component can be allocated. Everything
that was verified upstream of this — cook, retoc, mount, load, and the whole
`AudioComponent` API — is unusable because there is no device underneath it.
`GetAudioTimeSeconds` advancing is just the world audio clock and means nothing.

**The only remaining in-engine route** is replacing `.wem` payloads inside BL4's own
Wwise banks, in the 53.6 GB legacy `.pak` side. That fits how BL4 modding actually
works (overrides, which are proven) and there are 91 cue slots to overwrite. You can
only replace tracks, never add them, and the audio must fit the existing
event/segment structure. FModel bundles vgmstream for decoding `.wem`; re-encoding
needs community Wwise tooling.

## Resolved: containers can only override, never add

Two containers were built from the same cooked asset with the same retoc
invocation, differing only in package name:

| container | package | result |
|---|---|---|
| `ProbeTone_P` | `/Game/ProbeAudio/ProbeTone` (new) | never loadable |
| `ZZHijack_9999_P` | `.../WPLayer_Mus_Ambiance_CTY` (existing) | **loaded, class = SoundWave** |

That first test was confounded — `ProbeTone_P` had no priority number, and per
`../CLAUDE.md` a mod filename needs one or it loses to the base game. So the package
name and the filename convention changed together.

**Retested and confirmed.** Rebuilt as `ProbeTone_9998_P` — priority number present,
identical cooked asset, identical retoc invocation, only the filename changed — with
`ZZHijack_9999_P` left installed as a positive control. The hijack still loads; the
new path still does not. The filename was not the cause.

**Custom content must ship under the name of a package BL4 already has.**

## Rebuilding

Test fixture and cook project live in `cook/`. The UE project used was
`C:\bl4mod\OakGame` (named `OakGame` so cooked paths line up with BL4's).

```bash
python cook/make_wav.py cook/ProbeTone.wav
```

Import without touching the editor GUI (needs `PythonScriptPlugin` enabled in the
`.uproject`, which `cook/OakGame.uproject` does):

```bash
UnrealEditor-Cmd.exe <proj>.uproject -run=pythonscript -script=cook/import_probe.py -unattended -nopause
```

Cook — **both flags matter**:

```bash
UnrealEditor-Cmd.exe <proj>.uproject -run=Cook -targetplatform=Windows \
  "-COOKDIR=<abs path to content subdir>" "-MAP=/Game/X/YourMap" \
  -SkipZenStore -unattended -nopause
```

- `-SkipZenStore` is **mandatory**. UE 5.8 (and 5.5) cook to the Zen store by
  default; without it the cook reports success with 586 packages while
  `Saved/Cooked/` holds only a `Metadata` folder. The tells are `zenfs.manifest`,
  `ue.projectstore`, and `LogZenStore: Establishing oplog`. Four cooks were lost to
  this. Never read "no .uasset on disk" as "the cook failed" — check for Zen first.
- `-COOKDIR` cooks a directory regardless of whether anything references it. The
  cooker otherwise builds its list from *maps*, so an orphan asset is skipped
  silently. `-package=` does not help (it feeds the same list as `-map=`), and
  `DirectoriesToAlwaysCook` in `DefaultGame.ini` is ignored by this commandlet.
- `-COOKDIR` does **not** cook `.umap` files. Maps need `-MAP=`.

Pack:

```bash
retoc to-zen --version UE5_5 -f <filter> <cooked>/Windows out/Name_9998_P.utoc
```

## Engine version pitfalls

BL4 is **UE 5.5** (`fileVersionUE5 = 1013`, `LegacyFileVersion = -8`). Use UE 5.5.x.

**Do not cook with 5.8.** It writes `fileVersionUE5 = 1018` / `LegacyFileVersion = -9`,
and **retoc cannot parse it** — `to-zen --version` tops out at `UE5_7` and dies with
`Error: failed to fill whole buffer`. The `--version` flag selects the *output*
target; it does not teach retoc to read newer input. (`USoundWave`'s runtime
property layout is in fact identical between 5.5 and 5.8 — all 27 properties, same
order — so the incompatibility is purely tooling.)

`CanUseUnversionedPropertySerialization=False` under `[Core.System]` **crashes the
cooker** (`Assertion failed`, `UnversionedPropertySerialization.cpp:1003`). UE
requires unversioned properties when cooking. It is an Engine setting, not a
Packaging one, and is not exposed in the Project Settings UI.

Always set **`SoundAssetCompressionType = PCM`**. The default `PlatformSpecific`
resolves to BinkAudio, and a stock UE install may have no Bink encoder at all
(`Decoder for AudioFormat 'BINKA' not found`). PCM removes codec risk entirely; the
cooked `.ubulk` should be exactly `sample_rate * seconds * 2` bytes.

## The SDK mods

| mod | keys | what it does |
|---|---|---|
| `audio_probe/` | F8, F9 | dumps the music system and sweeps for load routes |
| `music_remap/` | F4, F5, F6, F12 | rewrites zone→switch mappings; F6 cycles contrasting cues |
| `music_watch/` | F2, F3 | hooks audio calls; F2 toggles `log_all_calls` (238 MB in seconds) |
| `probe_tone/` | F1, F11 | loads the cooked tone and tries every playback route |

Copy into `<game>/sdk_mods/`. Mods load **disabled** — enable in the `mods` console
menu (F10) or the keybinds do nothing. `ButtonOption.on_press` is passed the option
object, so callbacks must take one argument.

Raw probe output is in `findings/`.
