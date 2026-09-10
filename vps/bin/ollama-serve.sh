#!/bin/bash
# Ollama у user-space (без root): слухає лише 127.0.0.1, модель вивантажується одразу
# після запиту (KEEP_ALIVE=0), бо RAM на VPS ділиться з whisper.
export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_KEEP_ALIVE=0
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_NUM_PARALLEL=1
# Ті самі три ядра, що й у whisper: класифікація триває ~2,5 хв, і зайва
# швидкість тут нічого не варта, а вільне ядро під VPN і сервіси — варте.
export OLLAMA_NUM_THREAD=3
export OLLAMA_MODELS="$HOME/.ollama/models"

# cpulimit -l 250: те саме значення й та сама причина, що для whisper
# (WHISPER_CPU_LIMIT у process.py) — інцидент 012, 09.09.2026: llama-server,
# дочірній процес ollama serve, під час класифікації брав 370-385% із 400%
# можливих і просідав WireGuard Романа.
# На відміну від whisper, тут не можна обгорнути launch команди: llama-server
# спавнить сам ollama serve під час запиту, а не classify.py/process.py в
# момент виклику. Тому cpulimit працює у фоні за ІМ'ЯМ процесу (-e, -b) —
# ловить llama-server щоразу, як він з'являється. Короткий пік між появою
# процесу й тим, як cpulimit його помітить (внутрішній опитувальний цикл),
# лишається можливим — це межа самого механізму SIGSTOP/CONT, той самий
# компроміс, що вже прийнятий для whisper.
# Гейт проти дубля (при ручному рестарті, як в інциденті 012) і сам запуск
# монітора винесені в окремий скрипт — його ж періодично викликає crontab,
# щоб самозцілитись, якщо монітор колись упаде між перезавантаженнями VPS.
"$HOME/lectures/bin/ensure-ollama-cpulimit.sh"
command -v cpulimit >/dev/null 2>&1 || \
    echo "cpulimit не в PATH — Ollama БЕЗ обмеження CPU (встанови: apt install cpulimit)" >&2

exec "$HOME/.local/bin/ollama" serve
