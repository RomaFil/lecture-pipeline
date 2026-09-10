#!/bin/bash
# Перевіряє сам МЕХАНІЗМ cpulimit (SIGSTOP/CONT), яким обмежено і whisper
# (WHISPER_CPU_LIMIT у process.py), і llama-server (ollama-serve.sh).
#
# Не чіпає бойові whisper/ollama — власний одноядерний навантажувач,
# обмежений через `cpulimit -p PID`, той самий механізм, що й `-e NAME`
# у проді (ціль лише інакше знаходиться, сам SIGSTOP/CONT — той самий).
#
# Привід написати: жорсткий cgroup CPUQuota мовчки НЕ спрацював на цьому
# VPS (контролер cpu не делеговано, 09.09.2026) — тобто "перевірено
# спрацьованою" тут не про здогад, а про машину, на якій реально працює.
set -u
PASS=0; FAIL=0
check() { if [ "$1" = 0 ]; then PASS=$((PASS+1)); echo "  OK   $2"; else FAIL=$((FAIL+1)); echo "  FAIL $2 ($3)"; fi; }

if ! command -v cpulimit >/dev/null 2>&1; then
    echo "cpulimit не в PATH — тест пропущено (встанови: apt install cpulimit)"
    exit 0
fi

bash -c 'while :; do :; done' &
TARGET=$!
sleep 1

cpu_unlimited=$(ps -o %cpu= -p "$TARGET" 2>/dev/null | tr -d ' ')
cpu_unlimited=${cpu_unlimited:-0}
awk -v c="$cpu_unlimited" 'BEGIN{exit !(c>60)}'
check $? "без обмеження навантажувач реально вантажить ядро" "${cpu_unlimited}%"

cpulimit -l 30 -p "$TARGET" -b >/dev/null 2>&1 &
LIMITER=$!
sleep 3

cpu_limited=$(ps -o %cpu= -p "$TARGET" 2>/dev/null | tr -d ' ')
cpu_limited=${cpu_limited:-0}
awk -v c="$cpu_limited" 'BEGIN{exit !(c<60)}'
check $? "з cpulimit -l 30 CPU суттєво нижче некерованого рівня" "${cpu_limited}%"

kill "$LIMITER" 2>/dev/null
kill "$TARGET" 2>/dev/null
wait "$LIMITER" "$TARGET" 2>/dev/null

echo
echo "ПІДСУМОК: пройдено $PASS, провалено $FAIL"
[ "$FAIL" -eq 0 ]
