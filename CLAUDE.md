# BL4 Music Patch

Goal: give BL4 the ambient and combat music it lacks, ideally by importing BL2/BL3
tracks.

**Status: the UE route is dead; the Wwise route is open — and is not what it
looked like.** BL4 does not initialise Unreal's native audio mixer, so custom
`USoundWave`s can never be played, however cleanly they cook, pack and load
(*Answered: the mixer is off*). But BL4's `.wem` media are **loose files in the
legacy paks**, not payloads buried inside `.bnk`. Replacing music is therefore an
ordinary file override — the same mechanism as the proven `.css` mod — and needs
no bank surgery at all. See *The Wwise side*.

> Packaging rules, `.ncs` handling and safety live in the parent `../CLAUDE.md`.
> This file covers only audio.

## Work offline first (2026-10-01)

Do not learn the music system by launching the game. Everything below can be
seen and heard from disk.

- **wwiser** (`../tools/wwiser.pyz`, bnnm, v20260808) parses BL4's v145 banks
  with zero errors. `-d txt` dumps every field **with its byte offset**, which
  is what in-place bank patching needs. `-g` writes `.txtp` files that
  vgmstream (FModel's copy) plays. That includes layers, playlists, random
  intros and loops, so any cue can be heard as Wwise sequences it. Use `-te`
  for short names (full titles go in `!tags.m3u`) and `-gd` when every
  path matters, because by default identical paths are kept only once.
  `-gw` is relative to the txtp folder.
- **Names come from the game.** NCS tables `wwise_switches`, `wwise_states`
  and `audio_event` list every Wwise name, which gives
  `data/bl4/wwnames.txt` (115k names). With it, 163 of 184 cues are named; the
  other 21 are wildcard branches.
- **The music logic is NCS too.** `gbxmusic` (`Music_Oak2_Common` in chunk 4,
  per-region defs in chunk 6) sets the threat that starts combat music:
  light 9/4 (enter/exit), heavy 26/20, critical 15/7.5. Rank weights: chump
  0.5, elite 1.5, badass 3, boss 10. Lowering the light threshold is a
  small NCS edit, separate from any audio work.
- **What a BL4 combat cue is:** a random 4-7 s intro hit, cut from a long
  file by trims, then a 64 s loop of up to 5 stacked layers. 59% of combat
  segments play 3 or more tracks at once. So a replacement puts the new
  music on one layer and silence on the others, and the loop length
  (segment duration and markers, f64 fields) is patched in the bank.

### Source games

| game | audio | names | music logic |
|---|---|---|---|
| BL2 | AKPK `.pck`, `wwise/akpk.py` (BL2's header has the 4th "externals" size field) | bank names from STID chunks (`Ice_P_SFX`...); `music_states` = `music_ambience` / `music_combat` / `music_boss_0N`, recovered by FNV brute force | one switch per map |
| BL3 | `pakchunk2` + patches, AES key from the BLCM wiki, unpack oldest to newest | asset names in the pak index: `WwiseState_<group>_<value>`, `WwiseBank_<bank>`, `WE_<event>`; story-DLC banks recovered by brute force (`Mus_Dandelion_*`, `Mus_Geranium_*`, `Mus_Alisma_*`, GL&T events in bank `Music`) | `OakMusicData` assets give each `Mus_System_Sections` value a threat range; parts play in order |
| Wonderlands | not installed (only saves in its folder) | | |

`wwise/tools/ue4json` dumps BL3 UE4 assets to JSON (UAssetAPI, tagged
properties, no usmap); `data/bl3/sections.json` holds the threat ranges.

### The listening library

`python wwise/listen.py [bl2] [bl3] [bl4]` renders one MP3 per area and state
into `listen/`, gain-matched to -16 LUFS. BL3 sections are rendered as suites,
with the parts back to back. Silent placeholders and stingers are dropped.
That gives 558 tracks (22.8 h), rendered in about two minutes. The page is
`listen/index.html`. Serve it with `listen/serve.py`, which supports Range
requests so seeking works; it is also the `music-picker` entry in the
game folder's `.claude/launch.json`. Picks live in localStorage; "Copy for
Claude" puts them on the clipboard.

## Building a music mod (`wwise/musicmod.py`)

`python wwise/musicmod.py mods/blmix.json [--install]` puts library tracks into
BL4 slots. The spec lists combat containers (per-biome or Vaults; types basic
and critical) and zone containers (Fadefields `943367025`, Terminus Range
`244616023`), with track ids as `listen/tracks.js` prints them. First mod:
`BLMusicMix_9700_P`, 2026-10-01. v1 and v2 were heard in game, but most new
tracks stayed silent; see *The preload rule* below, fixed in v3. v3 is
confirmed in game: Vaults, all ripper areas and Fadefields combat play. Its
exploration was still silent (the threat-gated riser, see v2 below); v4
fixes that.

### Combat drop-outs (v5, 2026-10-02)

The user report was that combat music faded out while enemies were still
around but a bit far, then restarted from the top about 5 s later. Two causes,
two fixes:

- **The game ends combat too early.** The threat thresholds are NCS
  (`Music_Oak2_Common`, `gbxmusic4.ncs`, only in `pakchunk4-Windows_0_P`). All
  23 region defs inherit them; only Tuba overrides heavy and critical.
  - Ranges (enter/exit): light 9/4 (this one ends combat music), heavy 26/20,
    critical 15/7.5.
  - An enemy's threat falls with distance down to 0.125
    (`threatscaleatmaxdistance`).
  - Other fields from the exe's reflection strings, not set in NCS:
    `ThreatScaleDistance`, `ThreatCutoffDistance`,
    `GameplayMusicFadeTimeSeconds`.
  - `wwise/musicncs.py` lowers the light exit (spec `threat.light_exit`, now
    1.0) and packs the file at `Engine/Content/_NCS/`. It carves the newest
    copy by raw bytes, finds the cell by matching the shown (enter, exit) pair
    in the NCS_TRACE output, repoints it with `ncs_multipatch.py`, requires a
    one-value JSON diff and writes it stored.
  - **The mod now carries an NCS file**: rebuild it after any patch that
    touches pakchunk4, or it reverts that file.
- **The music restarted.** Every rule that starts a combat track said "start
  of playlist, entry marker". The spec's `resume` sets the "any -> any" and
  "nothing -> any" rules of all 11 region containers and the Vaults to
  `eEntryType` 4, LastExitTime ("Sync to: Last exit position"), with a 300 ms
  fade-in. That is what BL4's own TOP ambient container uses (6 s fade-in
  there). The exploration track's "any -> riser" rule resumes too.
- Not yet heard in game.

### The preload rule (v3, 2026-10-01)

**Every bank source declares `uInMemoryMediaSize` = its file's header (all
bytes before the audio) + its first 6 Opus packets.** That holds for all
2,839 Opus music sources in the shipped maingame bank, without exception.
The game preloads that many bytes and parses the header (with the whole seek
table, one u16 per 20 ms) from them.

- v1 and v2 repointed sources to new files but kept the old files' sizes. A
  2-minute track has a 12.6 KB header and the 8:51 Wetlands 53 KB, while most
  combat stems declare 6-9 KB. Every track whose header did not fit was silent
  in game. It still rendered fine offline, because vgmstream ignores this
  field.
- In v2 that silenced Ripper B critical, Ripper C and D, Vaults critical and
  all exploration. Fadefields combat, Ripper B basic and Vaults basic played,
  because their media ids carried 27 KB sizes from other sources.
- The game keys the preload by media id: v1's Ripper B played through a
  host source declaring 7 KB, because other sources of the same id declared
  27 KB. So **every source that references a written id gets the new size**,
  including silenced stems and unreachable leftovers.
- `wemopus.prefetch_size()` computes the size. `musicmod.py` checks the rule
  on the shipped bank first (a BL4 patch that changes it stops the build),
  then patches the sizes and re-checks every music source of the result.
- Hedge: media ids are chosen so that their *original* declared size also
  holds the new header where possible. The game's paks contain no media
  table besides the banks, so this should not matter.

Everything is patched **in place** in the maingame bank `1731745708.bnk`
(207 fields; no byte moves; wwiser re-reads it as the same objects):

- **Combat playlist**: random intro items -> the loop segment (u32 SegmentID).
  Loop segment: `fDuration` and the entry/exit markers (`43573010`/`1539036744`)
  are set to the new track. The host stem's source -> the new media, with
  playAt/trims 0 and srcDuration = the track. Other stems -> one silence file.
- **Falling leaf -> the combat playlist.** The per-biome container has
  `bIsContinuePlayback=1`, so the track carries on through the cool-down. Then
  "any -> nothing" fades it at the next bar over 2 s. Combat <-> falling and
  basic <-> critical switch at NextGrid (1 s) with a 400 ms fade, not at the
  exit cue, so long tracks do not make music overstay a fight.
- **Zones**: every leaf of the zone container -> one host playlist.
- **Host stem** = clean (one clip, no automation, RTPC or state chunk) and
  the **loudest** (Volume + MakeUpGain props). BL4 balances stems down to
  -7 dB. A quiet host forced Vaults critical through a limiter, 4 dB short.
- **Media ids**: only ids whose every user, in every bank
  (`data/bl4/media_usage.json`), is unreachable or repointed. In one biome,
  combat, critical and falling share 5 long stem files (different windows),
  so stems can never simply be overwritten.
- **Sources**: rendered via a copy of the TXTP with `commands = #v 0.5`.
  wwiser's auto +16-18 dB clips dense BL3 layer stacks in vgmstream's 16-bit
  output. All picks were 48 kHz with loop = whole stream, so cuts are
  sample-exact (the script refuses otherwise). `wemopus.py` now writes the
  exact sample count (last Ogg granule minus pre-skip), like BL4's own files.
- **Loudness**: each patched playlist is rendered at `-gv 1.0` and compared
  with the original. Up to 4 passes; the result is within 0.2 dB on all 8 slots.

**Verification**: TXTP comparison keyed by event + gamesyncs, with `-gd` and
playback lines only. 108 paths changed, all inside the picked slots; no
surviving path references the overwritten media. The only side effect is that
the Murder Mystery "body drop" stinger is gone (it hung off that zone's
playlist). Watch for **dedupe artefacts**: with plain `-g`, paths appear and
vanish when duplicate relationships shift. `underwaterfacility` (CTY) shares
music with zones in GR, MNT and SH.

The preview for "Terminus zones" first rendered the **Mountains Fortress**
(`Mus_Ambient_MNT_Fortress`, `mnt_fortress_a-d`, its own container). Restrict
zone renders to `Mus_WorldP_DefaultGameplay`.

**Overriding the whole maingame bank** means a BL4 patch that changes it gets
reverted by our stale copy. After any update that touches `pakchunk2`:
re-run `index_audio.py`, extract banks, regenerate `data/bl4/txtp`, rebuild
`media_usage.json`, then re-run musicmod.

Failure signatures to expect in game: several tracks stacked or garbled means
the media loaded but not the bank; nothing changes at all means the pak never
mounted.

### v2 (2026-10-01): exploration everywhere, all ripper areas

The v1 Vaults music was **confirmed in game**: the patched maingame bank
loads, not just the media.

**Exploration between named places cannot come from the zone switches.**
- Outside a POI, each ambient layer sets `<zone group>:default`, and the top
  ambient tree has no music for all-default.
- The tree checks `gr` before `mnt`, `sh` and `cty`, and switches keep their
  value after you leave a layer's painted area. So any "play something on
  default" would leak into other regions.
- The paint-layer assets can't be edited structurally either. UAssetAPI
  reads `OakWorldPainterLayer_MusicZone` as a RawExport, because its class
  is not in the usmap.

**The region-safe lever is `mus_biome`.**
- The combat container `815672541` plays in every state, everywhere the
  WorldPZones layer paints a biome. Per-biome containers hold leaves for
  basic or critical x combat or falling, plus a wildcard-type leaf for
  `rising`.
- Re-keying that rising leaf to 0 ("any state") makes ambient and rising play
  its playlist. That playlist, the riser, becomes the exploration track. Its
  clip fade curves are flattened to unity in place (`AkRTPCGraphPoint.To`).
- This relies on Wwise's best-match fallback to the wildcard combat type,
  which BL4's own rising leaf already depends on. wwiser resolves trees the
  same way: exact key first, then the wildcard, with backtracking.
- **The riser is threat-gated (found after v3: silent everywhere in the
  Fadefields).** Each riser playlist carries three `Music_Threat` curves:
  volume, LPF and HPF. At zero threat the volume is -1.0, which is silent:
  dB-scaled curves (`eScaling` 2) are stored normalized to -1..1, as all
  1,568 such curves in the bank show. It also plays on its own bus
  `3406085544` (-2 dB, speaker panning for the build-up). v4 sets those
  curves to neutral (0) and sends the playlist to `1443811164`, the bus BL4's
  zone music plays on (the bus of `level_from`).
- Bus map (Init bank `1355168291`), under Music:
  - `1533192012`: TOP combat container, no curves.
  - `1443811164`: zone music (TOP ambient).
  - `3733692670`: the risers, ripperD and Vaults combat.

  The last two both follow RTPC `2476962059`, a global music gate (probably
  `Mus_MuteSystemMusic`). It is open in play, since zone music and Vaults
  combat both play. **Offline renders apply neither RTPCs nor buses**, so
  check those by reading the bank.

**Avoiding double playback.**
- Non-faction zones of the region's zone container now play nothing
  (audioNodeId 0), because the biome track already covers them.
- Order and Ripper zones keep the track. Their biome is a faction variant
  (`orderA-C`, `ripperA-D`), which has no exploration leaf.

**All fortresses force their biome** through a `GbxMusicAction_SetSwitch` in
gbxmusic6. Fadefields, Mountains and UpperCity fortresses set `orderC`;
Shatterlands sets `ripperD`. The biome exploration track therefore never plays
on top of a fortress's own music.

**Ripper A-D are generic variants painted across regions**, so v2 puts
Windshear Waste on all four.

**Top combat container rule**: any->any was ExitMarker with no fades, so a
biome change waited for the playing loop to end (up to 8:51 now). It is now
NextBar with 2 s fades.

**Verification is built in** (`verify_paths`):
- `-gd` TXTPs, keyed by event + gamesyncs.
- Each changed path must pass through a patched playlist, a bypassed
  falling playlist or a zone container. Whole biome containers don't count,
  because their untouched leaves must stay identical.
- No path outside those may play an overwritten media id.
- Known losses must be listed in the spec's `accept_losing`.
- v2: 137 of 681 paths changed, 0 stray, 0 leaks.

`wwise/modpage.py` renders the after-previews and writes `listen/mod.js`.

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

**The remaining in-engine route is the Wwise side**, described below — with one
correction to what this file used to say: it does *not* require editing bank media,
because the `.wem` are loose files. You can still only replace cues, never add
them, and the audio has to fit the existing segment structure.

## The Wwise side

**Every `.wem` is a loose file.** `OakGame/Content/WwiseAudio/Media/<id>.wem`,
764,789 distinct paths across the legacy paks (57,226 of them non-localised; the
rest are voice, per language). Banks are separate and tiny — 29 `.bnk`, 12 MB
total — and hold only structure, no music payload. Replacing a track is a plain
file override, exactly like `EpicsLargerFirmwareLocks_5010_P` overriding a `.css`.

**All audio lives in `pakchunk2`.** Nothing else needs scanning. The master music
bank is `1731745708.bnk` (7.3 MB): 601 music segments, 2757 music tracks, 35 music
switch containers, 220 playlists.

**1612 `.wem` are music — 1.29 GB.** Stereo 48 kHz **Wwise Opus** (`fmt` tag
`0x3041`), long-form: median clip 57 s, the big ambient pieces 7:05. Everything
else (SFX, VO) is **Wwise Vorbis** (`0xffff`). **BL4 ships no PCM or ADPCM `.wem`
at all**, so do not assume the runtime can decode them — an injected PCM `.wem` is
an untested gamble, not a shortcut.

### repak cannot read three of BL4's paks — and one of them matters

`pakchunk2-Windows_{2,5,21}_P.pak` make `repak` panic
(`index out of bounds ... 18446744071562067967`). They are patch paks containing
**deletion records**: the full directory index stores `INT32_MIN` as the entry
index to mean "this file is gone". repak reads that as an array offset and dies,
and `repak list` silently yields nothing for them, so they vanish from any
manifest built that way.

That is not academic: **`pakchunk2-Windows_21_P` holds the newest master music
bank.** It is byte-for-byte the same size as the v20 copy and differs in 22 bytes,
so nothing looks wrong — analysis just silently runs on a stale bank. 65 files are
deleted by patches this way.

`wwise/pakx.py` parses the index itself (deletions included) and extracts through
Oodle; it was validated byte-identical against repak on a pak repak *can* read.
**Newest-wins still applies: highest `_<M>_P` wins.**

### Reading the bank (Wwise v145)

`wwise/bank.py`. Three things cost time and are not in the obvious references:

- **`AkTrackSrcInfo` gained a `u32 eventID`** at bank version ≥ 140, between
  `sourceID` and the four `f64` time fields. Miss it and 2689 of 2757 music tracks
  fail to parse — and the clip durations come out as absurd numbers rather than
  erroring.
- **A `u8 eMode` sits between `uTreeDataSize` and the `AkDecisionTree` itself.**
  Off-by-one here still "parses": every node reads shifted by a byte, and the tell
  is weights of 12800/25600 instead of Wwise's defaults of 50/100.
- **Walk the hierarchy upward.** `Children` lists sit behind fully variable
  `NodeBaseParams` tails, but `DirectParentID` is at a small computable offset from
  the *start* of `NodeBaseParams`. Going up from tracks avoids parsing state chunks
  and RTPCs entirely. Validation is free: every parent must be the type Wwise's
  model allows (track→segment→playlist→switch container), and it is, 2757/2757.

### Cue names

Wwise stores only FNV-1 32-bit hashes of lowercased names; BL4 ships no
`SoundbanksInfo`. Names come from two places:

1. **Cooked `uasset` name tables.** `retoc to-legacy -f Mus` over the containers
   yields `Music.<group>:<value>` strings — 149 of them, 9 groups. The layer assets
   are in `pakchunk6`/`pakchunk4`, not `pakchunk0`.
2. **Hashing candidates against the ids in the bank** for the rest.

17 switch groups, all named (`mus_gameplay_state` is the master — its subtree
reaches all 1612 files; `mus_combat_type` × `mus_biome` selects combat music).
**Treat brute-forced names with suspicion**: a 600M-candidate sweep over a 32-bit
space produces collisions, and it returned `mus_herbs_pail` for group 2363331616,
which is not a plausible name. `arjay_boss` and `mus_gameplay_state` are kept only
because independent event/bank strings corroborate them.

`findings/cue_to_wem.json` — 184 cues, 89 fully named, covering all 1612 files.

### Why BL4's music is missing

**The four zone switch groups have no default branch.** Their decision trees carry
no `key == 0` fallback and no value hashing to `default`. The painted world zones
set `Music.mus_ambiance_zone_CTY:default` and friends — a value the bank does not
define — so Wwise matches nothing and plays silence. `mus_combat_type` *does* carry
`key == 0` branches, which is why combat music works and zone ambience does not.

This is the mechanism behind "91 cues exist, the game plays a handful". It also
means a remap mod (pointing zones at real switch values) is a genuinely separate
fix from replacing audio.

### The Wwise Opus container, reverse-engineered

Every music `.wem` is the same shape, and almost every field is a constant. Across
40 sampled files: 48 kHz, stereo, `fmt` tag `0x3041`, `fmt` chunk 36 bytes.

```
RIFF .... WAVE
  fmt  36   wFormatTag=0x3041  nChannels=2  nSamplesPerSec=48000
            nAvgBytesPerSec    nBlockAlign=0  wBitsPerSample=0
            cbSize=18  frameSize=960  A=12546  totalSamples  seekCount
            B=312  C=1  D=0
  hash 16   (present on every file)
  seek N*2  one u16 per Opus packet - its size in bytes
  data      Opus packets, concatenated, no framing
```

Only **three** things vary: `totalSamples`, `seekCount`, and the seek/data pair.
`seekCount == ceil(totalSamples/960) + 1`, and **the seek entries sum exactly to
the data chunk size** — verified on several files, so the table is nothing but
per-packet byte lengths and the packets carry no framing of their own. 960 samples
is a plain 20 ms Opus frame at 48 kHz.

Minimal layout is `fmt hash seek data` (33 of 40 files). `akd`, `cue` and `LIST`
appear on a few and are not needed.

So producing a `.wem` from arbitrary audio is: encode 48 kHz stereo Opus in 20 ms
frames, strip the Ogg framing to get raw packets, write the packet sizes as the
seek table, and fill in two numbers. **No Wwise install is required** — and
`vgmstream-cli` decodes the result, so an encoder can be validated offline by
round-tripping against the source WAV before the game is ever launched.

Not yet checked: whether the 16-byte `hash` value matters at runtime or is only
an authoring-time asset hash.

### Building an override

`wwise/build_override.py` takes a spec naming cues or `.wem` ids and emits
`pak` + the stub `utoc`/`ucas` that makes it mount. Reusing one of the game's own
music `.wem` is the only substitution with no codec risk.

```bash
python wwise/index_audio.py                      # newest-wins audio index
python wwise/map_cues.py                         # cue -> .wem map
python wwise/build_override.py probe_combat_swap.json BL4MusicProbe_9700_P build
```

### Proven: the `.wem` override works

A probe pak carrying 1612 replaced music files **and** a `main_menu_button.css`
override turned the main-menu text magenta *and* made the in-game music buzz. The
CSS was the positive control: magenta proves the pak mounted and that loose-file
overrides resolve, and audible corruption proves Wwise read the replaced `.wem`
rather than BL4's. **Always ship a positive control in a diagnostic pak** — without
it, "I heard nothing" cannot be told apart from "the pak never mounted", and that
ambiguity already cost one inconclusive test.

There are **no logs to fall back on**: BL4 is a shipping build with file logging
disabled. Nothing is written to the game folder, `%LOCALAPPDATA%`, or
`Documents\My Games\Borderlands 4\Saved\Logs` (crash-reporter logs only). NTFS
last-access times are enabled but throttled to roughly hourly, so they cannot
show whether a given launch read a given pak. Diagnostics have to be built *into*
the mod.

### A replacement must be at least as long as the segment declares

Each `MusicTrack` clip carries `fSrcDuration`, and Wwise's music engine is
sample-accurate. Point a source at a file shorter than its segment expects and
playback runs past end of file — that is the buzzing, not a codec problem.

Declared durations across the 1606 sources: min 6.7 s, median 57.2 s, max 425 s.
A single 55 s file used for everything is short for **53%** of them.

Not the cause, checked and ruled out: **no prefetch is embedded in the banks.**
`uInMemoryMediaSize` is declared per source (984–45,182 B), but those head bytes
appear in none of the 29 banks, so nothing splices bank audio onto the streamed
file. The declared size still matters, because the game preloads that many
bytes of the loose file and needs the whole header in them. See *The preload
rule*.

### And it must actually contain audio

**BL4 ships silent filler `.wem`.** 21 of the 1606 music sources encode to about
**2 kbps** — Opus compressing pure silence. They are, by a wide margin, the
smallest files that are long enough for anything, so "cheapest track at least as
long as needed" selects them for nearly every target. That is what made combat
music *disappear* in probe v3: the duration fix worked, the buzzing stopped, and
every slot was pointed at digital silence. Measured: −240 dB RMS and peak.

Select on **content as well as length** — `wwise/pick_replacements.py` enforces
both, requiring ≥ 60 kbps. Bitrate is a reliable proxy here: 1119 of 1606 sources
sit in the normal 60–140 kbps band, and anything under ~20 kbps is a sparse pad
rather than music.

The general lesson: optimising a probe for pak size selects for silence, which is
indistinguishable from the mod not working. Optimise for *legibility* instead.

Remove the three `BL4MusicProbe_9700_P.*` files from `Paks/` to revert.

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

Only one is still live. The other three answered their question and were
uninstalled from the game; they stay here because their findings are recorded
above and re-running them is the only way to re-check those.

| mod | keys | status |
|---|---|---|
| `music_remap/` | F4, F5, F6, F12 | **live, and now the interesting one** — rewrites zone→switch mappings. Since the zone groups have no `default` branch, pointing zones at real switch values is what would turn BL4's zone music *on*; it is the companion to replacing audio, not a rival to it |
| `audio_probe/` | F8, F9 | retired — dumped the music system and swept for load routes. There are none |
| `music_watch/` | F2, F3 | retired — hooked audio calls and proved none ever fire (F2 toggles `log_all_calls`, 238 MB in seconds) |
| `probe_tone/` | F1, F11 | retired — tried every playback route for a cooked tone. The mixer is off |

Copy into `<game>/sdk_mods/`. Mods load **disabled** — enable in the `mods` console
menu (F10) or the keybinds do nothing. `ButtonOption.on_press` is passed the option
object, so callbacks must take one argument.

Raw probe output is in `findings/`.

`cook/` and `build/` likewise belong to the dead UE route. They are kept only as
the record of a pipeline that provably works up to the point where BL4 has no
audio device to play into; nothing in the Wwise route uses them.
