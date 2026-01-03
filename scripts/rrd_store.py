import rrdtool # Installed via Sudo apt as this is a Windows C depencie


def update(flow, ret, delta, burner, modulation):
    rrdtool.update("rrd/heating.rrd",
                   f"N:{flow}:{ret}:{delta}:{burner}:{modulation}")
