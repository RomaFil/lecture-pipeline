#!/usr/bin/env python3
"""Дрі-ран класифікатора на фікстурах. Модель тримається в пам`яті між викликами."""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("LECTURE_KEEP_ALIVE", "5m")
sys.path.insert(0, str(Path(__file__).parent))
import classify as clf  # noqa: E402

# файл → (очікуваний код дисципліни, очікуваний тип заняття)
# НЕВІДОМО = має піти в _needs-review; kind=None = тип не перевіряємо
# (на негативних кейсах він не має значення, бо запис і так не поїде на YouTube).
#
# Тип перевіряється з 07.09.2026: без нього enum kind мовчки деградував —
# практики ставали "лекціями", бо третього варіанту в схемі просто не було.
EXPECTED = {
    "t1_schemo.txt": ("СХЕМОТЕХНІКА", "лекція"),
    "t2_kola.txt": ("ТЕОРІЯ_КІЛ", "лекція"),
    "t3_fizyka.txt": ("ФІЗИКА", "лекція"),
    "t4_matan.txt": ("МАТАНАЛІЗ", "лекція"),
    "t5_cisco_lab.txt": ("МЕРЕЖІ_CISCO", "лабораторна"),
    "t6_matan_practice.txt": ("МАТАНАЛІЗ", "практика"),
    "t7_otk_practice.txt": ("ТЕОРІЯ_КІЛ", "практика"),
    "n1_negative_cook.txt": ("НЕВІДОМО", None),
    "n2_negative_eng.txt": ("НЕВІДОМО", None),
}

fixtures = Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures")
print(f"модель: {clf.MODEL}\n")
passed = 0
for name, (want, want_kind) in sorted(EXPECTED.items()):
    text = (fixtures / name).read_text(encoding="utf-8")
    t0 = time.time()
    try:
        res = clf.classify(text)
    except Exception as e:  # noqa: BLE001
        print(f"{name:24} ПОМИЛКА: {e}")
        continue
    dt = time.time() - t0
    raw = res.get("raw", {})
    got = str(raw.get("subject", "?"))
    kind = str(raw.get("kind", "—"))
    conf = raw.get("confidence", "—")
    ok = got == want and (want_kind is None or kind == want_kind)
    passed += ok
    mark = "PASS" if ok else "FAIL"
    want_str = want if want_kind is None else f"{want}/{want_kind}"
    got_str = got if want_kind is None else f"{got}/{kind}"
    print(f"{mark} {name:24} want={want_str:24} got={got_str:24} conf={conf:5} "
          f"{dt:5.1f}s  {raw.get('topic', '—')}")
    if not ok:
        print(f"     keywords: {raw.get('keywords', '—')}")

print(f"\nпройдено {passed}/{len(EXPECTED)}")
sys.exit(0 if passed == len(EXPECTED) else 1)
