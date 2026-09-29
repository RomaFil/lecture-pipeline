#!/bin/bash
# Точка входу конвеєра. Викликається з Windows по SSH одразу після push,
# і додатково з cron як страховка (якщо push був, а тригер не дійшов).
set -u
export LECTURES_HOME="$HOME/lectures"
export WHISPER_MODEL="${WHISPER_MODEL:-large-v3}"
export WHISPER_LANG="uk"
export LECTURE_LLM="${LECTURE_LLM:-qwen2.5:7b-instruct-q4_K_M}"
export OLLAMA_URL="http://127.0.0.1:11434"

# Паралельна нарізка + декодування «варіант E» (29.09.2026, погоджено Романом).
# Контроль на 4 повних лекціях (Ajax, Матаналіз, ОТК, CISCO) проти старого режиму
# best_of=5 / 2.4 / prev=1 без нарізки:
#   0.43-0.77x замість ~1.6x; повтори рядків 6-37 замість 274-576 (у B = 3/4.0/prev=1);
#   пік RSS воркера 2.7-3.6 ГБ замість 4.2; справжніх втрат тексту (блоки ≥15 слів,
#   вирівнювання по словах) 0-2 на лекцію, ~1% слів — старий режим губить стільки ж, але інші.
# condition_on_previous_text=0 — петля не перетікає в наступне вікно; поріг 4.0 —
# диктування (compression_ratio 2.8-3.0) не вмикає fallback, петля (5-9) — так;
# best_of=3 — стеля пам'яті, якщо fallback усе ж спрацює; no_speech=none — тишу ріже VAD
# (на тексті не впливає: E і C збіглись на 99.6-100%).
# 28.09 цей режим (як «C») відкочували через «випалі вікна» — то була хиба метрики:
# довгі сегменти по 25-30 с рахувались як дірки між таймкодами. Деталі — [[lecture-pipeline]].
# Відкат — прибрати ці 5 рядків (бекап run.sh.bak-20260928 на VPS).
export CHUNK_ENABLE="${CHUNK_ENABLE:-1}"
export WHISPER_BEST_OF="${WHISPER_BEST_OF:-3}"
export WHISPER_CR_THRESHOLD="${WHISPER_CR_THRESHOLD:-4.0}"
export WHISPER_CONDITION_PREV="${WHISPER_CONDITION_PREV:-0}"
export WHISPER_NO_SPEECH_THRESHOLD="${WHISPER_NO_SPEECH_THRESHOLD:-none}"

# Ollama живе в user-space (root-доступу немає) — піднімаємо, якщо впав
if ! curl -fsS --max-time 5 "$OLLAMA_URL/api/version" >/dev/null 2>&1; then
    echo "$(date -Is) ollama не відповідає, запускаю" >> "$LECTURES_HOME/logs/ollama.log"
    setsid nohup "$LECTURES_HOME/bin/ollama-serve.sh" \
        >> "$LECTURES_HOME/logs/ollama.log" 2>&1 < /dev/null &
    for _ in $(seq 1 30); do
        curl -fsS --max-time 2 "$OLLAMA_URL/api/version" >/dev/null 2>&1 && break
        sleep 2
    done
fi

# nice поверх обмеження потоків. Потоки дають ЄМНІСТЬ (одне ядро завжди вільне),
# nice дає ПРІОРИТЕТ: навіть на своїх трьох ядрах конвеєр поступається всьому
# інтерактивному й латентно-чутливому — крипто WireGuard, xray, докерним сервісам.
# Коли машина вільна, ціни в цього немає: планувальник усе одно віддає весь час
# єдиному охочому.
exec nice -n 10 "$LECTURES_HOME/venv/bin/python" "$LECTURES_HOME/bin/process.py"
