#!/bin/bash
# Точка входу конвеєра. Викликається з Windows по SSH одразу після push,
# і додатково з cron як страховка (якщо push був, а тригер не дійшов).
set -u
export LECTURES_HOME="$HOME/lectures"
export WHISPER_MODEL="${WHISPER_MODEL:-large-v3}"
export WHISPER_LANG="uk"
export LECTURE_LLM="${LECTURE_LLM:-qwen2.5:7b-instruct-q4_K_M}"
export OLLAMA_URL="http://127.0.0.1:11434"

# Паралельна нарізка + декодування «варіант C» (28.09.2026, погоджено Романом).
# Нічний замір на повній ОТК-лекції 114 хв проти старого best_of=5 / 2.4 / prev=1:
#   0.82x замість 2.38x, 0 петель, сміття 2 сегменти замість 5, 98% слів еталона,
#   пік used 7.9 ГБ, мін. avail 3.7 ГБ (старий режим з нарізкою — 361 МБ).
# condition_on_previous_text=0 — петля не перетікає в наступне вікно (саме так
# temperature=0 27.09 з'їв половину лекції); поріг 4.0 — диктування викладача
# (compression_ratio 2.8-3.0) більше не вмикає fallback, справжня петля (5-9) — так;
# best_of=3 — стеля пам'яті, якщо fallback усе ж спрацює. Деталі — [[lecture-pipeline]].
# Відкат — прибрати ці 4 рядки (бекап run.sh.bak-20260928 на VPS).
export CHUNK_ENABLE="${CHUNK_ENABLE:-1}"
export WHISPER_BEST_OF="${WHISPER_BEST_OF:-3}"
export WHISPER_CR_THRESHOLD="${WHISPER_CR_THRESHOLD:-4.0}"
export WHISPER_CONDITION_PREV="${WHISPER_CONDITION_PREV:-0}"

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
