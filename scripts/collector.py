import subprocess


def read(cmd):
    r = subprocess.run(["ebusctl", "read", cmd], capture_output=True, text=True)
    return float(r.stdout.strip())
