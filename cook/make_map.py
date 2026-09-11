# Creates a tiny level that hard-references ProbeTone.
#
# Why: pyunrealsdk cannot call any load-by-path function (SoftObjectProperty has
# no path setter, and load_package is a no-op in this SDK build - proven against
# BL4's own packages). But LevelStreamingDynamic.LoadLevelInstance takes a plain
# StrProperty and is not latent, so it IS callable. Loading a map drags its
# dependencies in via Unreal's own loader, so a map that references the sound
# gets the sound loaded for us.
import unreal

MAP = "/Game/ProbeAudio/ProbeMap"
SOUND = "/Game/ProbeAudio/ProbeTone"

les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

les.new_level(MAP)

sound = unreal.EditorAssetLibrary.load_asset(SOUND)
if not sound:
    unreal.log_error("MAP: could not load %s" % SOUND)
else:
    actor = eas.spawn_actor_from_class(unreal.AmbientSound, unreal.Vector(0.0, 0.0, 0.0))
    if not actor:
        unreal.log_error("MAP: failed to spawn AmbientSound")
    else:
        comp = actor.get_editor_property("audio_component")
        comp.set_editor_property("sound", sound)
        # Do not let it autoplay when the level streams in - we want to trigger
        # playback ourselves, so the test measures the mixer and not the level.
        comp.set_editor_property("auto_activate", False)
        unreal.log("MAP: AmbientSound wired to %s" % SOUND)

    les.save_current_level()
    unreal.log("MAP: saved %s" % MAP)

    deps = unreal.EditorAssetLibrary.find_package_referencers_for_asset(SOUND, False)
    unreal.log("MAP: referencers of the sound now: %s" % (deps,))
