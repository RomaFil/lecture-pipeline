#!/usr/bin/env python3
"""Дрі-ран класифікатора на фікстурах. Модель тримається в пам`яті між викликами."""
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("LECTURE_KEEP_ALIVE", "5m")
sys.path.insert(0, str(Path(__file__).parent))
import classify as clf  # noqa: E402


def refuse_if_busy():
    """Не стартувати, поки конвеєр має роботу. ЦЕ НЕ ПЕРЕСТРАХОВКА.

    Тест виставляє LECTURE_KEEP_ALIVE=5m, щоб не перезавантажувати модель між
    фікстурами, — і Ollama лишається в памʼяті на ~4,7 ГБ. На VPS із 5,9 ГБ це
    означає, що будь-яка транскрипція, яка стартує поруч, гине від OOM-кілера.

    09.09.2026 це сталося ДВІЧІ за один день, причому вдруге — вже після того,
    як застереження було написане в нотатці конвеєра. Текстове попередження
    виявилось недостатнім, тому перевірка живе тут, у коді: єдине, що надійно
    зупиняє людину (чи агента), яка поспішає, — відмова стартувати.

    FORCE=1 лишає лазівку для випадку, коли справді треба.
    """
    if os.getenv("FORCE") == "1":
        return
    base = Path(os.getenv("LECTURES_HOME", Path.home() / "lectures"))
    incoming = base / "incoming"
    pending = [p for p in incoming.glob("*") if p.is_file()] if incoming.is_dir() else []
    busy = subprocess.run(
        ["pgrep", "-f", str(base / "bin") + r"/(process|transcribe_worker)\.py"],
        capture_output=True, text=True).stdout.strip()
    if pending or busy:
        what = []
        if pending:
            what.append("у incoming/ лежать записи: " + ", ".join(p.name for p in pending))
        if busy:
            what.append("працює обробка (pid " + busy.replace("\n", ", ") + ")")
        print("ВІДМОВА: " + "; ".join(what), file=sys.stderr)
        print("Тест тримає модель у памʼяті й уб'є транскрипцію OOM-кілером.",
              file=sys.stderr)
        print("Дочекайся порожньої черги або запусти з FORCE=1, якщо впевнений.",
              file=sys.stderr)
        sys.exit(3)


refuse_if_busy()

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
    # Додані 09.09.2026 разом із дисциплінами АЯКС і БОКС.
    # t8 перевіряє ще й найнебезпечнішу плутанину: лекція про Linux повна слова
    # «мережа», і без явного правила модель тягнула б її в МЕРЕЖІ_CISCO.
    "t8_ajax_linux.txt": ("АЯКС", "лекція"),
    "t9_boks.txt": ("БОКС", "тренування"),
    # Довга фікстура (12 000 символів) — ЄДИНА, що перевіряє шлях зі зрізом
    # "початок + середина". Побудована як реальний випадок 08.09.2026: двадцять
    # хвилин оргвступу й розповіді про компанію, далі три години про Linux і FHS.
    # Старий підхід (перші 5000 символів) бачив лише вступ і давав тему про
    # компанію. Тут перевіряється, що дисципліна лишається правильною, — а що
    # тема стала змістовною, видно в самому виводі тесту.
    "t10_long_intro.txt": ("АЯКС", "лекція"),
    "n1_negative_cook.txt": ("НЕВІДОМО", None),
    "n2_negative_eng.txt": ("НЕВІДОМО", None),
    # Негативний кейс саме під БОКС: спорт узагалі не став пропуском у список.
    # Без нього правило «спорт — це бокс» тихо ловило б будь-яке фізвиховання.
    "n3_negative_sport.txt": ("НЕВІДОМО", None),
}

fixtures = Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures")
print(f"модель: {clf.MODEL}\n")
passed = 0
for name, (want, want_kind) in sorted(EXPECTED.items()):
    text = (fixtures / name).read_text(encoding="utf-8")
    t0 = time.time()
    try:
        res = clf.classify_voted(text)
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
