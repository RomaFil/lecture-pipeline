"""Один прогін: бойовий process.transcribe() у власній сесії (група процесів = PID)."""
import json
import os
import sys
import time
from pathlib import Path

os.setsid()  # runner вбиває всю групу (python + cpulimit + воркери) однією командою
sys.path.insert(0, "/home/admin1/lectures/bin")
import process  # noqa: E402

wav, out, dur = Path(sys.argv[1]), sys.argv[2], float(sys.argv[3])
t0 = time.time()
d = process.transcribe(wav, dur)
wall = time.time() - t0
d["wall_s"] = round(wall)
with open(out + ".tmp", "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False)
os.replace(out + ".tmp", out)
print("WALL", round(wall), "coef", round(wall / dur, 3), "segments", d["segments"], flush=True)
