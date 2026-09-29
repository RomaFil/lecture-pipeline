"""QA прогону validate3 (пакетний режим faster-whisper проти режиму E).
Використання: qa3.py RUN -> друкує підсумок, пише RUN.verdict.json

Три осі:
- втрати: блоки ≥15 слів старого транскрипту (vault), яких нема в кандидаті, після
  вирівнювання по словах (метод align2.py з validate2); те саме для E на тому ж записі;
- вади: transcript_quality.assess() зі звуком (дірки, петлі рядків, сміття) +
  петлі ВСЕРЕДИНІ рядка (пакетний режим пише один рядок на VAD-шматок, і петля
  T=0 там — це повтор фрази в одному рядку, а не серія рядків);
- пам'ять і час.
"""
import difflib
import json
import os
import re
import sys

from pathlib import Path

sys.path.insert(0, "/home/admin1/lectures/bin")
import transcript_quality as tq  # noqa: E402

N = Path(os.environ.get("NIGHT_DIR", "/home/admin1/lectures/_thread_test/validate3"))
T = N.parent
run = sys.argv[1]
conf = {l.split()[0]: l.split() for l in (N / "runs.conf").read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.startswith("#")}
_, BATCH, SHA = conf[run][0], int(conf[run][1]), conf[run][2]
# E на тому ж записі: ОТК і CISCO — з validate2, Фізика — прогін FE цієї ночі
E_OUT = {"2c68bb7ac865": T / "validate2/E3.out.json", "41fd74b47222": T / "validate2/E4.out.json",
         "8fd88e8e1e14": N / "FE.out.json"}.get(SHA, N / "none")


def stamped(p: Path) -> str:
    t = p.read_text(encoding="utf-8")
    return json.loads(t)["stamped"] if p.suffix == ".json" else t


def dedup(r):
    out = []
    for s, t in r:
        k = " ".join(re.findall(r"\w+", t.lower()))
        if k and k in [" ".join(re.findall(r"\w+", x.lower())) for _, x in out[-5:]]:
            continue
        out.append((s, t))
    return out


def words(r):
    return [(w, s) for s, t in r for w in re.findall(r"\w+", t.lower())]


def losses(base, cand):
    sm = difflib.SequenceMatcher(None, [w for w, _ in base], [w for w, _ in cand], autojunk=False)
    miss, extra = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("delete", "replace") and (i2 - i1) - (j2 - j1) >= 15:
            miss.append((base[i1][1], (i2 - i1) - (j2 - j1), " ".join(w for w, _ in base[i1:i2])[:160]))
        if op in ("insert", "replace") and (j2 - j1) - (i2 - i1) >= 15:
            extra.append((cand[j1][1], (j2 - j1) - (i2 - i1), " ".join(w for w, _ in cand[j1:j2])[:160]))
    return sm.ratio(), miss, extra


def inline_loops(rows):
    """Петля всередині рядка: якась 4-грама слів повторюється ≥4 рази в одному рядку.
    (zlib-стиснення тут не годиться: кирилиця в UTF-8 має спільні провідні байти й
    стискається сильніше за латиницю — звичайне мовлення давало хибні спрацьовування.)"""
    out = []
    for s, t in rows:
        w = re.findall(r"\w+", t.lower())
        if len(w) < 16:
            continue
        grams = {}
        for i in range(len(w) - 3):
            g = tuple(w[i:i + 4])
            grams[g] = grams.get(g, 0) + 1
        if max(grams.values()) >= 4:
            out.append((s, t[:100]))
    return out


out = json.loads((N / f"{run}.out.json").read_text(encoding="utf-8"))
rows = tq.parse_rows(out["stamped"])
dur = float(out.get("duration") or 0)
sil = tq.parse_silences((N / f"sil_{SHA}.txt").read_text(), dur) if (N / f"sil_{SHA}.txt").exists() else None
q = tq.assess(out["stamped"], dur, sil)
il = inline_loops(rows)

base = words(dedup(tq.parse_rows((N / f"base_{SHA}.md").read_text(encoding="utf-8"))))
r_c, miss_c, extra_c = losses(base, words(rows))
e_line = ""
if E_OUT.exists() and run != "FE":
    r_e, miss_e, _ = losses(base, words(tq.parse_rows(stamped(E_OUT))))
    e_line = (f"   E на тому ж записі: збіг {r_e:.1%}, втрати {len(miss_e)} блоків / "
              f"{sum(x[1] for x in miss_e)} слів")
else:
    miss_e = None

mem = [l.split() for l in (N / f"{run}.mem.log").read_text().splitlines() if l.startswith("MEM")]
used, avail, rss = [int(x[2]) for x in mem], [int(x[3]) for x in mem], [int(x[4]) for x in mem]
coef = round(out.get("wall_s", 0) / max(dur, 1), 3)

v = {"run": run, "batch": BATCH, "sha": SHA, "coef": coef, "words": q["words"], "wpm": q["wpm"],
     "match": round(r_c, 3), "miss_blocks": len(miss_c), "miss_words": sum(x[1] for x in miss_c),
     "extra_blocks": len(extra_c), "extra_words": sum(x[1] for x in extra_c),
     "e_miss_blocks": None if miss_e is None else len(miss_e),
     "e_miss_words": None if miss_e is None else sum(x[1] for x in miss_e),
     "hole_minutes": q["hole_minutes"], "repeat_runs": q["repeat_runs"], "junk": q["junk"],
     "inline_loops": len(il), "peak_used_mb": max(used or [0]), "min_avail_mb": min(avail or [0]),
     "peak_worker_rss_mb": max(rss or [0])}
v["mem_ok"] = v["min_avail_mb"] >= 2000
(N / f"{run}.verdict.json").write_text(json.dumps(v, ensure_ascii=False))

print(f"{run} (batch={BATCH}, {SHA}): коеф {coef}x | слів {q['words']} ({q['wpm']:.0f}/хв) | збіг зі старим {r_c:.1%}")
print(f"   втрати проти старого: {len(miss_c)} блоків / {v['miss_words']} слів | зайві: {len(extra_c)} / {v['extra_words']} слів")
if e_line:
    print(e_line)
print(f"   вади: дірки {q['hole_minutes']} хв, петлі рядків {q['repeat_runs']}, петлі в рядку {len(il)}, сміття {q['junk']}")
print(f"   пам'ять: пік used {v['peak_used_mb']} МБ, мін avail {v['min_avail_mb']} МБ, пік RSS воркера {v['peak_worker_rss_mb']} МБ")
for s, n, t in miss_c[:4]:
    print(f"   - {s // 60}:{s % 60:02d} -{n}: {t[:110]}")
for s, t in il[:3]:
    print(f"   ∞ {s // 60}:{s % 60:02d} {t}")
