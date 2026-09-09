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
# Скільки ядер віддавати whisper. Без цього параметра faster-whisper бере ВСІ
# (тут 4), і транскрипція насмерть займає машину на години — а на ній же живуть
# WireGuard, xray і докерні сервіси. Жорстку межю через cgroup поставити не можна:
# перевірено 09.09.2026, контролер cpu користувачу не делеговано, і `systemd-run
# --user -p CPUQuota=` мовчки приймає межу, не застосовуючи її (два цикли під
# квотою 100% дали ~190%).
#
# Три, а не чотири — і це коштує менше, ніж здається. За семплером СЕРЕДНЄ
# споживання whisper на чотирьох потоках було ~301 %, тобто робота і так
# вкладалась приблизно в три ядра: решта часу процес чекав на памʼять, а не на
# CPU. Тому четверте ядро лишається вільним майже безкоштовно.
WHISPER_THREADS = int(os.getenv("WHISPER_THREADS", "3"))


def main(wav: str, out: str) -> int:
    model = WhisperModel(MODEL, device="cpu", compute_type="int8",
                         cpu_threads=WHISPER_THREADS)
    segments, info = model.transcribe(wav, language=LANG, vad_filter=True, beam_size=1)

    stamped, plain = [], []
    for s in segments:
        m, sec = int(s.start) // 60, int(s.start) % 60
        text = s.text.strip()
        stamped.append(f"[{m:02d}:{sec:02d}] {text}")
        plain.append(text)

    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "stamped": "\n".join(stamped),
            "plain": " ".join(plain),
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
