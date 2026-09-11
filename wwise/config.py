"""Paths and constants for the BL4 Wwise toolkit."""
import os

GAME  = r"C:\Program Files (x86)\Steam\steamapps\common\Borderlands 4"
PAKS  = os.path.join(GAME, "OakGame", "Content", "Paks")
OODLE = r"C:\Users\alexa\.cargo\bin\oo2core_9_win64.dll"
REPAK = r"C:\repak\repak.exe"
RETOC = os.path.expanduser(r"~\.cargo\bin\retoc.exe")
VGMSTREAM = r"C:\fmodel\Output\.data\vgmstream-cli.exe"

# All of BL4's audio lives in pakchunk2's legacy paks.
AUDIO_PAK_GLOB = "pakchunk2-Windows_*_P.pak"
# Master music bank: 601 segments, 2757 tracks, 35 music switch containers.
MUSIC_BANK = "1731745708.bnk"
WWISE_DIR = "../../../OakGame/Content/WwiseAudio/"

# Where generated data lands (override with BL4_AUDIO_DATA).
DATA = os.environ.get("BL4_AUDIO_DATA",
                      os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))

def data(*parts):
    os.makedirs(DATA, exist_ok=True)
    return os.path.join(DATA, *parts)
