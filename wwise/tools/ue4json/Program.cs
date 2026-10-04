// ue4json: dump a cooked UE4 asset with tagged (versioned) properties to JSON.
// BL3 and Wonderlands assets need no .usmap.
//   ue4json <asset.uasset> <out.json> [EngineVersion, default VER_UE4_20]
using System;
using System.IO;
using UAssetAPI;
using UAssetAPI.UnrealTypes;

class Program
{
    static int Main(string[] args)
    {
        if (args.Length < 2) { Console.Error.WriteLine("usage: ue4json <asset.uasset> <out.json> [EngineVersion]"); return 2; }
        var ver = args.Length > 2 ? Enum.Parse<EngineVersion>(args[2]) : EngineVersion.VER_UE4_20;
        var asset = new UAsset(args[0], ver);
        File.WriteAllText(args[1], asset.SerializeJson(true));
        return 0;
    }
}
