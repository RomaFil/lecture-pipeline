#!/usr/bin/env python3
"""Транскрипція одного wav. Запускається ОКРЕМИМ процесом і одразу вмирає.

Так зроблено навмисно: whisper large-v3 у int8 тримає ~2.9 ГБ, і на цьому VPS
(5.8 ГБ разом) вони мусять повернутися до ОС ще до того, як Ollama візьме свої
~4.7 ГБ під 7B-модель. Вихід процесу — єдина гарантія, що пам`ять справді звільнено;
del + gc.collect() лишає її в malloc-аренах.

Використання: transcribe_worker.py <wav> <out.json>
"""
import json
import os
import sys

from faster_whisper import WhisperModel

MODEL = os.getenv("WHISPER_MODEL", "large-v3")
LANG = os.getenv("WHISPER_LANG", "uk")
# Скільки ядер віддавати whisper. Без цього параметра faster-whisper бере ВСІ,
# і транскрипція насмерть займає машину на години — а на ній же живуть
# WireGuard, xray і докерні сервіси. Жорстку межю через cgroup поставити не можна:
# перевірено 09.09.2026, контролер cpu користувачу не делеговано, і `systemd-run
# --user -p CPUQuota=` мовчки приймає межу, не застосовуючи її (два цикли під
# квотою 100% дали ~190%).
#
# 22.09.2026: спершу піднято 3 -> 4 (VPS давно на 6 ядер, з 10.09.2026 —
# process.py вже тоді підняв WHISPER_CPU_LIMIT до 400, просто ніхто не підняв
# WHISPER_THREADS услід). Але 4 виявилось ГІРШЕ за 3: whisper_cpu впав до
# ~170-200% (замість ~276-300% на 3-х), час зріс до ~2,5х. Піднято до 5 як
# "компроміс із запасом" — теж виміряно і теж погано: на реальному запуску
# (FS-theory 37хв відео, Intro-QA1 7.5хв відео) коефіцієнт часу 1.98х і 2.84х,
# whisper_cpu за 190 семплів avg=176% max=259% — не краще за яму на 4-х, подекуди
# гірше. Тобто погана зона ширша за рівно "4" і накриває як мінімум 4-5.
# Це не наша поломка: задокументована відома вада самого faster-whisper/CTranslate2
# — на 4 потоках продуктивність аномально погана, на 1 і 8 знову нормальна
# (SYSTRAN/faster-whisper issues #526, #599; те саме на іншому залізі, Intel Xeon,
# відповіді від мейнтейнерів нема). cpu_threads=0 (авто) є СПРАВЖНІМ дефолтом
# бібліотеки, не 3, не 4, не 5.
# Рішення (22.09.2026, Роман): повернуто на 3 — єдине на той момент підтверджене
# хороше значення (whisper_cpu 276-300%).
#
# 26.09.2026: дозмірено решту діапазону на тому самому 10-хв тестовому кліпі.
# 1 потік — 3.8х (гірше за все). cpu_threads=0 (справжній авто-дефолт
# бібліотеки) — 3.475х, теж погано. 6 потоків під `cpulimit -l 400` — не
# завершився за 50+ хв (>=5х), вбито вручну. **2 потоки — 1.623х, ~191% CPU:
# швидше за 3 І легше по CPU.** Єдине значення, що побило тодішній дефолт по
# обох осях одразу. Тому новий дефолт — 2.
# Якщо колись повернешся сюди тестувати ще — 4, 5, 6, 0(авто) і 1 уже зміряні
# й усі гірші за 2 і 3. Лишається хіба перехід на whisper.cpp (інша кодова
# база, ggml, не CTranslate2) як більший проект.
# Паралельна нарізка (2 шматки по 2 потоки, розріз по паузі) дала 1.085х —
# майже реальний час, але це вже не крутилка тут, а окрема фіча
# `chunked_transcribe.py` (`CHUNK_ENABLE`), дизайн — [[lecture-pipeline]].
# Джерело метрик і повне розслідування — [[lecture-pipeline]], розділи про
# 22.09.2026 і 26.09.2026.
WHISPER_THREADS = int(os.getenv("WHISPER_THREADS", "2"))


def main(wav: str, out: str) -> int:
    model = WhisperModel(MODEL, device="cpu", compute_type="int8",
                         cpu_threads=WHISPER_THREADS)
    segments, info = model.transcribe(wav, language=LANG, vad_filter=True, beam_size=1)

    stamped, plain, raw = [], [], []
    for s in segments:
        m, sec = int(s.start) // 60, int(s.start) % 60
        text = s.text.strip()
        stamped.append(f"[{m:02d}:{sec:02d}] {text}")
        plain.append(text)
        raw.append({"start": s.start, "text": text})

    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "stamped": "\n".join(stamped),
            "plain": " ".join(plain),
            # raw_segments — додано 26.09.2026 для chunked_transcribe.py: без
            # сирих секундних start потрібно було б регексом парсити [MM:SS]
            # з stamped, щоб зсунути час при злитті шматків. process.py й усе
            # інше нижче за течією це поле не читають — суто адитивне,
            # наявний контракт (stamped/plain/segments/…) не змінився.
            "raw_segments": raw,
            "segments": len(stamped),
            "language": info.language,
            "language_probability": round(info.language_probability, 3),
            "duration": round(info.duration, 1),
            "model": MODEL,
        }, f, ensure_ascii=False)
    print(f"segments={len(stamped)} lang={info.language} "
          f"p={info.language_probability:.2f} dur={info.duration:.0f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
