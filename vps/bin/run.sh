#!/bin/bash
# Точка входу конвеєра. Викликається з Windows по SSH одразу після push,
# і додатково з cron як страховка (якщо push був, а тригер не дійшов).
set -u
export LECTURES_HOME="$HOME/lectures"
export WHISPER_MODEL="${WHISPER_MODEL:-large-v3}"
export WHISPER_LANG="uk"
export LECTURE_LLM="${LECTURE_LLM:-qwen2.5:7b-instruct-q4_K_M}"
export OLLAMA_URL="http://127.0.0.1:11434"

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

exec "$LECTURES_HOME/venv/bin/python" "$LECTURES_HOME/bin/process.py"
