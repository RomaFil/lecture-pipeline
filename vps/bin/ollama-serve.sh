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
if command -v cpulimit >/dev/null 2>&1; then
    # Захист від дубля: якщо скрипт перезапускають вручну (як в інциденті 012)
    # без перезавантаження VPS, попередній фоновий монітор ще живий — другий
    # поруч не додає користі, лише плутанину при діагностиці (два -e llama-server
    # незалежно ганяються за тим самим процесом).
    if ! pgrep -f 'cpulimit .*-e llama-server' >/dev/null 2>&1; then
        cpulimit -l 250 -e llama-server -b >/dev/null 2>&1 &
        disown
    fi
else
    echo "cpulimit не в PATH — Ollama БЕЗ обмеження CPU (встанови: apt install cpulimit)" >&2
fi

exec "$HOME/.local/bin/ollama" serve
