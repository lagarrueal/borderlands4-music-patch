# Cook our test tone under the package NAME of a BL4 asset that is already
# resident, so the container OVERRIDES an existing package instead of ADDING a
# new one.
#
# Why: every working BL4 mod replaces a path the game already loads; none adds a
# new package. Adding requires BL4's global package store to gain an entry at
# mount time, which it may simply not do - that would explain why our correctly
# built container is never seen. Overriding reuses a chunk id that already
# exists, so if THAT works, the hypothesis is confirmed.
#
# Target: WPLayer_Mus_Ambiance_CTY (city ambient music layer). Confirmed
# resident by the earlier music probe, and breaking city music is about the
# lowest-impact damage available. Deleting the pak restores it.
import unreal

WAV = r"C:\bl4mod\OakGame\ProbeTone.wav"
FOLDER = "/Game/GameData/Audio/Music/WorldPaintLayers"
NAME = "WPLayer_Mus_Ambiance_CTY"
PKG = FOLDER + "/" + NAME

task = unreal.AssetImportTask()
task.filename = WAV
task.destination_path = FOLDER
task.destination_name = NAME
task.automated = True
task.replace_existing = True
task.save = True

unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

asset = unreal.EditorAssetLibrary.load_asset(PKG)
if not asset:
    unreal.log_error("HIJACK: import failed for " + PKG)
else:
    asset.set_editor_property("sound_asset_compression_type",
                              unreal.SoundAssetCompressionType.PCM)
    unreal.EditorAssetLibrary.save_asset(PKG)
    unreal.log_warning("HIJACK: ready %s class=%s" % (PKG, asset.get_class().get_name()))
