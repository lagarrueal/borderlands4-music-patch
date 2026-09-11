# Imports the test tone and forces PCM, with no GUI steps.
#
# PCM matters: it removes codec risk entirely. BL4 exposes both bUseBinkAudio
# and SoundAssetCompressionType, and we do not know which decoders its build
# carries. The 5.8 attempt confirmed PCM cooks to raw bulk data - the .ubulk was
# exactly 352,800 bytes for 4s @ 44.1kHz 16-bit mono.
import unreal

WAV = r"C:\bl4mod\OakGame\ProbeTone.wav"
PKG = "/Game/ProbeAudio/ProbeTone"

task = unreal.AssetImportTask()
task.filename = WAV
task.destination_path = "/Game/ProbeAudio"
task.destination_name = "ProbeTone"
task.automated = True
task.replace_existing = True
task.save = True

unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

asset = unreal.EditorAssetLibrary.load_asset(PKG)
if not asset:
    unreal.log_error("PROBE: import failed, asset did not load")
else:
    try:
        asset.set_editor_property("sound_asset_compression_type",
                                  unreal.SoundAssetCompressionType.PCM)
        unreal.EditorAssetLibrary.save_asset(PKG)
        unreal.log("PROBE: imported and set PCM -> " + PKG)
    except Exception as e:
        unreal.log_error("PROBE: could not set PCM: %s" % e)
    unreal.log("PROBE: class=%s duration=%s" % (asset.get_class().get_name(),
                                                asset.get_editor_property("duration")))
