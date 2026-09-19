#!/usr/bin/env python3
"""Офлайн-перебір правил вибору `kind` на голосах, зібраних collect_votes.py.

    replay_kind.py votes.jsonl

Жодних викликів моделі й жодних залежностей: голоси по фрагментах уже лежать у
jsonl, а правила — чисті функції. Виводить таблицю «правило × запис» і підсумок,
щоб правило обиралось за даними, а не за здогадкою (дві прості гіпотези —
проста більшість і «kind з фрагмента теми» — вже зламали різні записи).
"""
import collections
import json
import sys


def winners(results):
    votes = [r["subject"] for r in results]
    win, cnt = collections.Counter(votes).most_common(1)[0]
    if cnt < 2:
        return None, []
    return win, [i for i, v in enumerate(votes) if v == win]


def topic_idx(idxs):
    """Те саме, що classify._pick_topic_index."""
    return next((i for i in idxs if i != 0), idxs[-1])


def rule_current(results, win, idxs):
    return results[topic_idx(idxs)]["kind"]


def rule_naive_majority(results, win, idxs):
    """Проста більшість по всіх трьох фрагментах (зламала t10 09.2026)."""
    return collections.Counter(r["kind"] for r in results).most_common(1)[0][0]


def _majority(kinds, fallback):
    if not kinds:
        return fallback
    top = collections.Counter(kinds).most_common()
    if len(top) > 1 and top[0][1] == top[1][1]:
        return fallback
    return top[0][0]


def rule_winners_only(results, win, idxs):
    """Більшість лише серед фрагментів, що проголосували за переможну дисципліну."""
    return _majority([results[i]["kind"] for i in idxs], results[topic_idx(idxs)]["kind"])


def rule_skip_unknown(results, win, idxs):
    """Як вище, але «невідомо» — це утримання від голосу, не голос."""
    kinds = [results[i]["kind"] for i in idxs if results[i]["kind"] != "невідомо"]
    return _majority(kinds, results[topic_idx(idxs)]["kind"])


def rule_all_skip_unknown(results, win, idxs):
    """Більшість по всіх фрагментах без «невідомо»; при нічиїй — kind фрагмента теми."""
    kinds = [r["kind"] for r in results if r["kind"] != "невідомо"]
    return _majority(kinds, results[topic_idx(idxs)]["kind"])


def rule_skip_intro(results, win, idxs):
    """Фрагмент 0 (оргвступ) за kind не голосує; при нічиїй — kind фрагмента теми."""
    kinds = [results[i]["kind"] for i in idxs if i != 0 and results[i]["kind"] != "невідомо"]
    return _majority(kinds, results[topic_idx(idxs)]["kind"])


def rule_weighted(results, win, idxs):
    """Вага: вступ 0.5, решта 1; «невідомо» — 0; нічия → kind фрагмента теми."""
    score = collections.Counter()
    for i in idxs:
        k = results[i]["kind"]
        if k != "невідомо":
            score[k] += 0.5 if i == 0 else 1.0
    if not score:
        return results[topic_idx(idxs)]["kind"]
    top = score.most_common()
    if len(top) > 1 and top[0][1] == top[1][1]:
        return results[topic_idx(idxs)]["kind"]
    return top[0][0]


RULES = [rule_current, rule_naive_majority, rule_winners_only, rule_skip_unknown,
         rule_all_skip_unknown, rule_skip_intro, rule_weighted]


def main(path):
    rows = [json.loads(ln) for ln in open(path, encoding="utf-8") if ln.strip()]
    rows = [r for r in rows if len(r["results"]) == 3]
    scored = {f.__name__: [0, []] for f in RULES}
    n = 0
    print(f"{'запис':34} {'правда':12} " + " ".join(f"{f.__name__[5:14]:>14}" for f in RULES))
    for r in rows:
        win, idxs = winners(r["results"])
        if win is None or win != r["truth_subject"] or not r["truth_kind"]:
            print(f"{r['file'][:34]:34} (subject не зійшовся: пропуск)")
            continue
        n += 1
        cells = []
        for f in RULES:
            got = f(r["results"], win, idxs)
            good = got == r["truth_kind"]
            scored[f.__name__][0] += good
            if not good:
                scored[f.__name__][1].append(r["file"])
            cells.append(f"{'ok ' if good else 'МИМО'}:{got[:8]:>8}")
        print(f"{r['file'][:34]:34} {r['truth_kind']:12} " + " ".join(f"{c:>14}" for c in cells))
    print(f"\nПІДСУМОК (записів із правильним subject: {n})")
    for f in RULES:
        ok, bad = scored[f.__name__]
        print(f"  {f.__name__:24} {ok}/{n}   помилки: {', '.join(bad) or '—'}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "votes.jsonl")
