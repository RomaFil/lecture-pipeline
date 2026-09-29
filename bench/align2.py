"""Справжні втрати по словах (не по таймкодах): блоки ≥15 слів еталона, яких нема в кандидаті.
Еталонні рядки-повтори (петлі старого режиму) спершу стискаються, щоб не рахувались як «втрати»."""
import json, re, sys, difflib
V = "/home/admin1/lectures/_thread_test/validate2/"
def rows(txt):
    return [(int(m.group(1))*60+int(m.group(2)), m.group(3)) for m in
            (re.match(r"\[(\d+):(\d+)\] ?(.*)", l) for l in txt.splitlines()) if m]
def dedup(r):  # однаковий текст рядка, що вже траплявся в останніх 5 рядках -> викинути
    out = []
    for s, t in r:
        k = " ".join(re.findall(r"\w+", t.lower()))
        if k and k in [" ".join(re.findall(r"\w+", x.lower())) for _, x in out[-5:]]: continue
        out.append((s, t))
    return out
def words(r): return [(w, s) for s, t in r for w in re.findall(r"\w+", t.lower())]
def load(p): return rows(open(p, encoding="utf-8").read() if p.endswith(".md") else json.load(open(p))["stamped"])
show = "-v" in sys.argv
for lect, runs in (("Ajax", ("E1", "B1")), ("Матаналіз", ("E2", "B2")), ("CISCO", ("E4", "B3"))):
    base = words(dedup(load(V + runs[0] + ".base.md")))
    print(f"===== {lect}: еталон (без повторів) {len(base)} слів")
    for R in runs:
        raw = load(V + R + ".out.json")
        C = words(raw); Cd = words(dedup(raw))
        sm = difflib.SequenceMatcher(None, [w for w, _ in base], [w for w, _ in C], autojunk=False)
        miss, extra = [], []
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op in ("delete", "replace") and (i2 - i1) - (j2 - j1) >= 15:
                miss.append((base[i1][1], (i2 - i1) - (j2 - j1), " ".join(w for w, _ in base[i1:i2])[:220]))
            if op in ("insert", "replace") and (j2 - j1) - (i2 - i1) >= 15:
                extra.append((C[j1][1], (j2 - j1) - (i2 - i1), " ".join(w for w, _ in C[j1:j2])[:220]))
        print(f"  {R}: збіг {sm.ratio():.1%} | слів {len(C)} (повтори рядків {len(C)-len(Cd)}) | "
              f"втрачені блоки {len(miss)} / {sum(x[1] for x in miss)} слів | зайві блоки {len(extra)} / {sum(x[1] for x in extra)} слів")
        if show:
            for s, n, t in miss: print(f"     - {s//60}:{s%60:02d} -{n}: {t}")
            for s, n, t in extra: print(f"     + {s//60}:{s%60:02d} +{n}: {t}")
