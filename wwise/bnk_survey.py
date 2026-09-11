import struct, sys, os, glob, collections

TYPE = {1:"State",2:"Sound",3:"Action",4:"Event",5:"RanSeqCntr",6:"SwitchCntr",
        7:"ActorMixer",8:"Bus",9:"LayerCntr",10:"MusicSegment",11:"MusicTrack",
        12:"MusicSwitchCntr",13:"MusicRanSeqCntr",14:"Attenuation",15:"DialogueEvent",
        16:"FeedbackBus",17:"FeedbackNode",18:"FxShareSet",19:"FxCustom",
        20:"AuxBus",21:"LFO",22:"Envelope",23:"AudioDevice",24:"TimeMod"}

def sections(b):
    o = 0
    while o + 8 <= len(b):
        tag = b[o:o+4].decode('ascii','replace')
        sz, = struct.unpack_from("<I", b, o+4)
        yield tag, o+8, sz
        o += 8 + sz

for path in sorted(glob.glob(sys.argv[1] + "/*.bnk")):
    b = open(path, "rb").read()
    if len(b) < 16: continue
    name = os.path.basename(path)
    secs = []
    hirc = collections.Counter()
    ver = None
    for tag, off, sz in sections(b):
        secs.append(f"{tag}:{sz}")
        if tag == "BKHD":
            ver, = struct.unpack_from("<I", b, off)
        if tag == "HIRC":
            n, = struct.unpack_from("<I", b, off)
            p = off + 4
            for _ in range(n):
                t = b[p]
                osz, = struct.unpack_from("<I", b, p+1)
                hirc[t] += 1
                p += 5 + osz
    h = " ".join(f"{TYPE.get(t,t)}={c}" for t, c in sorted(hirc.items()))
    print(f"{name:20} v{ver} [{' '.join(secs)}]")
    if h: print(f"{'':20} {h}")
