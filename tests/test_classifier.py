#!/usr/bin/env python3
"""Дрі-ран класифікатора на фікстурах. Модель тримається в пам`яті між викликами."""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("LECTURE_KEEP_ALIVE", "5m")
sys.path.insert(0, str(Path(__file__).parent))
import classify as clf  # noqa: E402

# файл → очікуваний код дисципліни (НЕВІДОМО = має піти в _needs-review)
EXPECTED = {
    "t1_schemo.txt": "СХЕМОТЕХНІКА",
    "t2_kola.txt": "ТЕОРІЯ_КІЛ",
    "t3_fizyka.txt": "ФІЗИКА",
    "t4_matan.txt": "МАТАНАЛІЗ",
    "t5_cisco_lab.txt": "МЕРЕЖІ_CISCO",
    "n1_negative_cook.txt": "НЕВІДОМО",
    "n2_negative_eng.txt": "НЕВІДОМО",
}

fixtures = Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures")
print(f"модель: {clf.MODEL}\n")
passed = 0
for name, want in sorted(EXPECTED.items()):
    text = (fixtures / name).read_text(encoding="utf-8")
    t0 = time.time()
    try:
        res = clf.classify(text)
    except Exception as e:  # noqa: BLE001
        print(f"{name:24} ПОМИЛКА: {e}")
        continue
    dt = time.time() - t0
    got = str(res.get("raw", {}).get("subject", "?"))
    ok = got == want
    passed += ok
    mark = "PASS" if ok else "FAIL"
    raw = res.get("raw", {})
    kind = raw.get("kind", "—")
    conf = raw.get("confidence", "—")
    print(f"{mark} {name:24} want={want:14} got={got:14} conf={conf:5} {dt:5.1f}s  "
          f"[{kind}] {raw.get('topic', '—')}")
    if not ok:
        print(f"     keywords: {raw.get('keywords', '—')}")

print(f"\nпройдено {passed}/{len(EXPECTED)}")
sys.exit(0 if passed == len(EXPECTED) else 1)
