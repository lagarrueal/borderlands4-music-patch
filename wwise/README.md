# BL4 Wwise toolkit

Reads BL4's audio out of the legacy paks, maps music cues to the `.wem` files
they play, and builds override mods. Background and pitfalls: `../CLAUDE.md`.

| file | what it does |
|---|---|
| `config.py` | paths to the game, repak, retoc, Oodle, vgmstream |
| `pakx.py` | pak v11 reader — handles the patch-deletion records repak chokes on, decompresses Oodle |
| `bank.py` | Wwise v145 bank reader: HIRC walk, music hierarchy, decision trees, FNV-1 hash |
| `index_audio.py` | → `data/audio_live.json`, the newest-wins copy of every audio file |
| `extract.py` | pull `.wem` / `.bnk` out by id |
| `map_cues.py` | → `data/cue_to_wem.json`, cue → `.wem` |
| `build_override.py` | spec → `.pak` + stub `.utoc`/`.ucas` |
| `bnk_survey.py` | section and HIRC-type histogram per bank |

```bash
python index_audio.py                # ~1 min, walks pakchunk2
python map_cues.py                   # extracts the master bank if needed
python extract.py wem data/wem 636658451
python build_override.py probe_combat_swap.json BL4MusicProbe_9700_P ../build
```

`data/` is generated and gitignored; every file in it rebuilds from the game.

## Replacement audio

BL4 ships only Wwise Vorbis (`0xffff`, SFX/VO) and Wwise Opus (`0x3041`, music).
Reusing one of the game's own music `.wem` is the only substitution known to be
safe. Bringing in outside audio needs a Wwise Opus encoder — or proof that the
runtime still decodes PCM, which BL4 itself never uses. `vgmstream-cli` decodes
`.wem` for inspection but does not encode.
