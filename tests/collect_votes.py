#!/usr/bin/env python3
"""Збір голосів класифікатора ПО ФРАГМЕНТАХ — сирі дані для підбору правила `kind`.

    collect_votes.py <out.jsonl> <файл>[::КОД::kind] ...

Для кожного транскрипту робить ті самі виклики моделі, що й classify_voted()
(chunks() + _request(), keep_alive як у бою), але замість підсумку зберігає
відповідь КОЖНОГО фрагмента: subject, kind, topic, confidence. Далі правила
голосування можна перебирати офлайн, без жодного нового виклику моделі
(див. replay_kind.py).

Правильна відповідь: для .md береться з фронтматеру (`дисципліна`, `тип`), для
інших файлів — з суфікса `::КОД::kind`. Файли, які вже є в out.jsonl, пропускаються,
тож перерваний прогін можна продовжити.

Тримає lock process.py на весь прогін: поки він іде, нові записи чекатимуть у
incoming (нічого не губиться), але whisper не стартує поруч із теплою Ollama —
на VPS дві моделі одночасно не живуть (OOM, 09.09.2026).

Запускати відвʼязано: setsid nohup ... python -u collect_votes.py ...
"""
import fcntl
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("LECTURES_BIN", os.path.expanduser("~/lectures/bin")))
import classify as c  # noqa: E402

BASE = Path(os.getenv("LECTURES_HOME", Path.home() / "lectures"))
FULL_TO_CODE = {v[0]: k for k, v in c.SUBJECTS.items()}


def load(spec: str):
    path, _, truth = spec.partition("::")
    text = Path(path).read_text(encoding="utf-8")
    if path.endswith(".md"):
        body = text.split("---", 2)[-1]
        plain = " ".join(re.sub(r"^\[\d+:\d+(:\d+)?\]\s*", "", ln).strip()
                         for ln in body.splitlines() if ln.strip().startswith("["))
        subj = re.search(r"^дисципліна: (.+)$", text, re.M)
        kind = re.search(r"^тип: (.+)$", text, re.M)
        return (plain, FULL_TO_CODE.get(subj.group(1).strip()) if subj else None,
                kind.group(1).strip() if kind else None)
    code, _, kind = truth.partition("::")
    return text, code or None, kind or None


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    out = Path(argv[0])
    done = set()
    if out.exists():
        done = {json.loads(ln)["file"] for ln in out.read_text(encoding="utf-8").splitlines() if ln}

    lock = open(BASE / ".process.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("process.py зараз працює (lock зайнятий) — не запускаю")
    if any((BASE / "incoming").iterdir()):
        sys.exit("в incoming щось лежить — не запускаю (OOM-ризик, див. інцидент 012)")

    for spec in argv[1:]:
        name = Path(spec.partition("::")[0]).name
        if name in done:
            print("пропуск (вже є):", name, flush=True)
            continue
        plain, truth_subject, truth_kind = load(spec)
        parts = c.chunks(plain)
        results = [c._request(c.SYSTEM, p, keep_alive=None if i == len(parts) - 1 else "30s")
                   for i, p in enumerate(parts)]
        row = {"file": name, "truth_subject": truth_subject, "truth_kind": truth_kind,
               "chunk_lens": [len(p) for p in parts],
               "results": [{k: r.get(k) for k in ("subject", "kind", "topic", "confidence")}
                           for r in results]}
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(name, truth_subject, truth_kind, "->",
              [(r.get("subject"), r.get("kind")) for r in results], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
