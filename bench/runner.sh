#!/bin/bash
# Нічний експеримент 29-30.09.2026: пакетний режим faster-whisper проти E. Запускається cron'ом кожні 10 хв; зайвий
# екземпляр одразу виходить (flock). Стан — у файлах RUN.status / RUN.attempts,
# тож після смерті runner'а чи ребуту робота продовжується з місця зупинки.
L=/home/admin1/lectures
T=$L/_thread_test
N=${NIGHT_DIR:-$T/validate3}
SRC_WAV=${SRC_WAV:-$T/otk114.wav}
RUNS=${RUNS:-"FE P1 P2 P3 P4"}
LOG=$N/runner.log
CRON_TAG="validate3-20260929"
MAX_ATTEMPTS=2
RUN_LIMIT_S=$((5 * 3600))
STALL_S=${STALL_S:-1200}
AVAIL_MIN=${AVAIL_MIN:-700}
AVAIL_START=${AVAIL_START:-8500}
DUR=${DUR:-6853.0}
NOT_BEFORE=${NOT_BEFORE:-$(date -d "2026-09-29 23:30" +%s)}

exec 9>"$N/runner.lock"
flock -n 9 || exit 0   # тримають і діти прогону: поки живий хоч один воркер — другого runner'а не буде
[ "$(date +%s)" -ge "$NOT_BEFORE" ] || exit 0   # до 23:30 cron-тики просто виходять — день належить проду

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
notify() {
    if [ "${NOTIFY:-1}" != 1 ]; then log "notify(вимкнено): $1"; return; fi
    "$L/bin/notify.sh" "$1" || log "notify не пройшов"
}
status() { cat "$N/$1.status" 2>/dev/null || echo pending; }

# сумарний CPU-час (тіки) усіх процесів групи — для детектора зависання
cpu_ticks() {
    local pg=$1 sum=0 f
    for f in /proc/[0-9]*/stat; do
        read -r -a s < "$f" 2>/dev/null || continue
        [ "${s[4]}" = "$pg" ] && sum=$((sum + s[13] + s[14]))
    done
    echo "$sum"
}

finalize_if_output() {  # прогін завершився, поки runner'а не було
    local R=$1
    if [ -f "$N/$R.out.json" ] && [ "$N/$R.out.json" -nt "$N/$R.started" ]; then
        log "$R: вихід є (прогін дожив без runner'а) — рахую QA"
        "$L/venv/bin/python" "$N/qa3.py" "$R" > "$N/$R.qa.txt" 2>&1
        echo done > "$N/$R.status"
        notify "Експеримент $R завершено (без runner'а)
$(cat "$N/$R.qa.txt")"
        return 0
    fi
    return 1
}

wait_mem() {  # $1=RUN $2=макс. секунд; класифікатор і Ollama (~4.7 ГБ) мають звільнити RAM
    local waited=0 av
    while :; do
        av=$(free -m | awk '/^Mem:/{print $7}')
        if [ "$av" -ge "$AVAIL_START" ] && ! pgrep -f "venv/bin/python tests/test_classifier.py" >/dev/null \
           && [ -z "$(ls -A "$L/incoming" 2>/dev/null)" ] && [ "$(date +%s)" -ge "$NOT_BEFORE" ]; then return 0; fi
        [ "$waited" -ge "$2" ] && { log "$1: пам'ять так і не звільнилась за $(( $2 / 60 )) хв (avail=$av) — стартую"; return 0; }
        [ $((waited % 600)) -eq 0 ] && log "$1: чекаю (avail=$av, треба $AVAIL_START; incoming: $(ls "$L/incoming" 2>/dev/null | wc -l) файл(ів); старт не раніше $(date -d @$NOT_BEFORE +%H:%M))"
        sleep 30; waited=$((waited + 30))
    done
}

run_one() {  # $1=RUN $2=batch $3=sha (джерело $T/audio/<sha>.wav); повертає 0 = готово, 1 = збій (можна повторити), 2 = збій без повтору
    local R=$1 BATCH=$2 SRC="$T/audio/$3.wav" BO=3 CR=4.0 CP=0 NS=none DUR
    local OUT=$N/$R.out.json MEM=$N/$R.mem.log RLOG=$N/$R.log
    rm -f "$OUT" "$N/$R.stats.jsonl"
    : > "$MEM"

    wait_mem "$R" 14400   # до 4 год: бойова лекція, класифікатор/Ollama, NOT_BEFORE

    exec 8>"$L/.process.lock"
    if ! flock -w 3600 8; then
        log "$R: lock конвеєра зайнятий > 1 год — спробую пізніше"; exec 8>&-; return 1
    fi
    wait_mem "$R" 900     # після lock: лекція перед нами могла лишити Ollama в пам'яті
    touch -d '+6 hours' "$L/state/maintenance"
    mkdir -p "$N/wav"
    ffmpeg -v error -y -i "$SRC" -vn -ar 16000 -ac 1 "$N/wav/input.wav" || { log "$R: ffmpeg не зміг витягти аудіо з $SRC"; rm -f "$L/state/maintenance"; exec 8>&-; return 2; }
    DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$N/wav/input.wav")
    touch "$N/$R.started"
    log "$R: старт batch=$BATCH (E: best_of=$BO cr=$CR prev=$CP no_speech=$NS) джерело $3"

    CHUNK_ENABLE=1 WHISPER_BEST_OF=$BO WHISPER_CR_THRESHOLD=$CR WHISPER_CONDITION_PREV=$CP WHISPER_NO_SPEECH_THRESHOLD=$NS WHISPER_BATCH=$BATCH \
    WHISPER_STATS_FILE="$N/$R.stats.jsonl" \
        nice -n 10 "$L/venv/bin/python" -u "$N/run_transcribe.py" "$N/wav/input.wav" "$OUT" "$DUR" > "$RLOG" 2>&1 &
    local PID=$! start now reason="" last_ticks=0 last_change ticks i=0
    start=$(date +%s); last_change=$start
    sleep 5
    while kill -0 "$PID" 2>/dev/null; do
        now=$(date +%s)
        read -r used av <<< "$(free -m | awk '/^Mem:/{print $3, $7}')"
        rss=$(ps -eo rss,args | awk '/[t]ranscribe_worker\.py/ && !/cpulimit/ {if ($1 > m) m = $1} END {print int(m / 1024)}')
        echo "MEM $(date +%T) $used $av $rss" >> "$MEM"
        if [ "$av" -lt "$AVAIL_MIN" ]; then reason="ram (avail=$av МБ)"; fi
        if [ $((now - start)) -gt $RUN_LIMIT_S ]; then reason="timeout (> $((RUN_LIMIT_S / 3600)) год)"; fi
        if [ $((i % 30)) -eq 0 ]; then   # раз на 5 хв: CPU-прогрес і heartbeat
            ticks=$(cpu_ticks "$PID")
            if [ "$ticks" -gt "$last_ticks" ]; then last_ticks=$ticks; last_change=$now; fi
            [ $((now - last_change)) -gt $STALL_S ] && reason="stall (CPU не росте $((STALL_S / 60)) хв)"
            log "$R: heartbeat $(( (now - start) / 60 )) хв, avail=$av, used=$used, rss=${rss}МБ, cpu_ticks=$ticks"
        fi
        if [ -n "$reason" ]; then
            log "$R: АВАРІЙНИЙ СТОП — $reason"
            kill -TERM -- "-$PID" 2>/dev/null; sleep 15; kill -KILL -- "-$PID" 2>/dev/null
            break
        fi
        i=$((i + 1)); sleep 10
    done
    wait "$PID" 2>/dev/null; local rc=$?
    rm -f "$L/state/maintenance" "$N/wav/input.wav"
    exec 8>&-

    if [ -z "$reason" ] && [ "$rc" -eq 0 ] && [ -f "$OUT" ]; then
        "$L/venv/bin/python" "$N/qa3.py" "$R" > "$N/$R.qa.txt" 2>&1
        log "$R: готово"; cat "$N/$R.qa.txt" >> "$LOG"
        notify "Експеримент $R (batch=$BATCH) — готово
$(cat "$N/$R.qa.txt")"
        return 0
    fi
    [ -z "$reason" ] && reason="впав (rc=$rc): $(grep -E 'Error|Traceback|Killed' "$RLOG" | tail -2)"
    echo "$reason" > "$N/$R.reason"
    notify "Експеримент $R (batch=$BATCH) — збій: $reason"
    case $reason in ram*) return 2 ;; esac
    return 1
}

log "runner старт (pid $$)"
for R in $RUNS; do
    st=$(status "$R")
    case $st in done|failed*) continue ;; esac
    if [ "$st" = running ]; then
        log "$R: попередню спробу перервано (runner помер або ребут)"
        finalize_if_output "$R" && continue
    fi
    while :; do
        att=$(cat "$N/$R.attempts" 2>/dev/null || echo 0)
        if [ "$att" -ge $MAX_ATTEMPTS ]; then
            echo "failed:attempts" > "$N/$R.status"
            notify "Експеримент $R: $MAX_ATTEMPTS спроби не вдались, переходжу далі. Причина: $(cat "$N/$R.reason" 2>/dev/null)"
            break
        fi
        echo $((att + 1)) > "$N/$R.attempts"
        read -r _ BATCH SHA <<< "$(grep "^$R " "$N/runs.conf")"
        echo running > "$N/$R.status"
        run_one "$R" "$BATCH" "$SHA"; rc=$?
        if [ $rc -eq 0 ]; then echo done > "$N/$R.status"; break; fi
        if [ $rc -eq 2 ]; then echo "failed:$(cat "$N/$R.reason")" > "$N/$R.status"; break; fi
        echo pending > "$N/$R.status"
        log "$R: спроба $((att + 1)) не вдалась, повтор"
    done
done

# усе завершено? — підсумок і прибрати себе з crontab
if [ "$(for R in $RUNS; do status $R; done | grep -cE "^(done|failed)")" -eq "$(echo $RUNS | wc -w)" ] && [ ! -f "$N/final.sent" ]; then
    notify "Нічний експеримент (пакетний режим) завершено
$(for R in $RUNS; do [ -f "$N/$R.qa.txt" ] && cat "$N/$R.qa.txt" || echo "$R: $(status $R)"; done)"
    touch "$N/final.sent"
    [ "${NO_CRON:-0}" = 1 ] || { crontab -l | grep -v "$CRON_TAG" | crontab -; }
    log "усе завершено, cron-рядок прибрано"
fi
