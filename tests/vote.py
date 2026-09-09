"""Класифікація голосуванням: початок, середина, кінець — і більшість.

Працює на СПРАВЖНІХ транскриптах, а не на фікстурах: саме вони показали, що
точність класифікатора на реальних записах — 6/8, тоді як на синтетичних
фікстурах 13/13. Фікстури короткі й чисті, справжні транскрипти довгі, з
обмовками, повторами й помилками розпізнавання.

Запуск:
    LECTURES_BIN=~/lectures/bin TRANSCRIPTS=~/lectures/eval-data         ~/lectures/venv/bin/python tests/vote.py

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

def chunks(text, n=5000):
    """Початок, середина, кінець — по n символів, по межі речення."""
    if len(text) <= n:
        return [text]
    mid = max(0, len(text) // 2 - n // 2)
    end = max(0, len(text) - n)
    return [c._trim_tail(text[:n]),
            c._trim_head(c._trim_tail(text[mid:mid + n])),
            c._trim_head(text[end:])]

def ask(text):
    r = requests.post(f"{c.OLLAMA}/api/chat", json={
        "model": c.MODEL,
        "messages": [{"role": "system", "content": c.SYSTEM},
                     {"role": "user", "content": "Стенограма заняття:\n\n" + text}],
        "format": c.SCHEMA, "stream": False, "keep_alive": "5m",
        "options": {"temperature": 0, "num_predict": 250, "num_ctx": 8192,
                    "repeat_penalty": 1.1}}, timeout=1800)
    return json.loads(r.json()["message"]["content"])

FULL = {v[0]: k for k, v in c.SUBJECTS.items()}
hit = total = unanimous = 0
for f in sorted(glob.glob(os.path.join(TRANSCRIPTS, "*", "*.md"))):
    plain, subj = plain_of(f)
    if not plain or not subj:
        continue
    want = FULL.get(subj, "?")
    total += 1
    votes, topics = [], []
    for part in chunks(plain):
        d = ask(part)
        votes.append(d.get("subject"))
        topics.append(d.get("topic"))
    cnt = collections.Counter(votes)
    win, n = cnt.most_common(1)[0]
    ok = win == want and n >= 2
    hit += ok
    if n == 3: unanimous += 1
    print("\n=== %s ===" % f.split("/")[-1][:64])
    print("  очікуємо %-13s голоси: %s" % (want, votes))
    print("  %s  %s (%d з 3)%s" % ("OK  " if ok else "МИМО", win, n,
                                    "" if n >= 2 else "  -> НЕМАЄ БІЛЬШОСТІ, _needs-review"))
    for i, t in enumerate(topics):
        print("     тема[%s]: %s" % (["початок", "середина", "кінець"][i], t))
print("\nПІДСУМОК ГОЛОСУВАННЯ: %d/%d (одностайних: %d)" % (hit, total, unanimous))
