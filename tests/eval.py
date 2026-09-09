"""Порівняння двох способів вибору фрагмента для класифікатора.

Працює на СПРАВЖНІХ транскриптах, а не на фікстурах: саме вони показали, що
точність класифікатора на реальних записах — 6/8, тоді як на синтетичних
фікстурах 13/13. Фікстури короткі й чисті, справжні транскрипти довгі, з
обмовками, повторами й помилками розпізнавання.

Запуск:
    LECTURES_BIN=~/lectures/bin TRANSCRIPTS=~/lectures/eval-data         ~/lectures/venv/bin/python tests/eval.py

TRANSCRIPTS — тека з .md-транскриптами конвеєра (у фронтматері має бути поле
`дисципліна`, воно й береться за правильну відповідь). Ці файли — записи чужих
занять, тому в репозиторії їх немає і бути не повинно.

ВАЖЛИВО: запускати відвʼязано, з логом у файл. Під час класифікації на VPS
лишається ~180 МБ вільної памʼяті, і SSH-сесія рветься посеред роботи —
див. інцидент 004.
"""
import os
sys.path.insert(0, os.environ.get("LECTURES_BIN",
                                  os.path.expanduser("~/lectures/bin")))
import classify as c
import requests

TRANSCRIPTS = os.environ.get(
    "TRANSCRIPTS", os.path.expanduser("~/lectures/eval-data"))

def plain_of(path):
    t = open(path, encoding="utf-8").read()
    b = t.split("---", 2)[-1]
    p = " ".join(re.sub(r"^\[\d+:\d+\]\s*", "", l).strip()
                 for l in b.splitlines() if l.strip().startswith("["))
    d = re.search(r"^дисципліна: (.+)$", t, re.M)
    return p, (d.group(1).strip() if d else None)

def middle_only(text, max_chars=5000):
    start = max(0, len(text) // 2 - max_chars // 2)
    return c._trim_head(c._trim_tail(text[start:start + max_chars]))

def ask(text):
    r = requests.post(f"{c.OLLAMA}/api/chat", json={
        "model": c.MODEL,
        "messages": [{"role": "system", "content": c.SYSTEM},
                     {"role": "user", "content": "Стенограма заняття:\n\n" + text}],
        "format": c.SCHEMA, "stream": False, "keep_alive": "5m",
        "options": {"temperature": 0, "num_predict": 250, "num_ctx": 8192,
                    "repeat_penalty": 1.1}}, timeout=1800)
    return json.loads(r.json()["message"]["content"])

FULL = {v[0]: k for k, v in c.SUBJECTS.items()}   # повна назва -> код
files = sorted(glob.glob(os.path.join(TRANSCRIPTS, "*", "*.md")))
old_ok = new_ok = total = 0
for f in files:
    plain, subj = plain_of(f)
    if not plain or not subj:
        continue
    want = FULL.get(subj, "?")
    total += 1
    print("\n=== %s ===" % f.split("/")[-1][:70])
    print("  очікуємо: %s (%d символів)" % (want, len(plain)))
    for label, text in (("СТАРЕ", plain[:5000]), ("СЕРЕДИНА", middle_only(plain))):
        d = ask(text)
        got = d.get("subject")
        hit = "OK " if got == want else "МИМО"
        if label == "СТАРЕ" and got == want: old_ok += 1
        if label == "СЕРЕДИНА" and got == want: new_ok += 1
        print("  %-9s %s %-13s conf=%-5s %s" % (label, hit, got, d.get("confidence"), d.get("topic")))
print("\nПІДСУМОК: старе %d/%d, середина %d/%d" % (old_ok, total, new_ok, total))
