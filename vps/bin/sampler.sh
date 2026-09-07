#!/bin/bash
# Семплер стану VPS — два знімки на хвилину (кожні 30 с).
#
# Навіщо: інцидент 004 (04.09.2026). SSH завис на 20 хв, вхід пройшов за частку
# секунди, команда не повернулась, а PSI і завантаження, зміряні постфактум,
# були цілком чисті. Бракувало знімка САМЕ в ту мить.
# Звіряти з рядком "WARN ssh: виклик перевищив 90 с" у Windows-лозі синка.
#
# Нічого не запускає і нікуди не ходить: тільки читання /proc і один ps.
# Один рядок ~200 Б, 2880 рядків на добу — близько 0.5 МБ/день, тримаємо 7 днів.

LOGDIR="$HOME/lectures/logs/sampler"
RETENTION_DAYS=7

mkdir -p "$LOGDIR"

sample() {
    local now file
    now=$(date '+%Y-%m-%d %H:%M:%S')
    file="$LOGDIR/sampler-$(date '+%Y-%m-%d').log"

    local l1 l5 l15 runs rest
    read -r l1 l5 l15 runs rest < /proc/loadavg

    local memav memfree swtot swfree swused
    memav=$(( $(awk '/^MemAvailable:/{print $2}' /proc/meminfo) / 1024 ))
    memfree=$(( $(awk '/^MemFree:/{print $2}' /proc/meminfo) / 1024 ))
    swtot=$(awk '/^SwapTotal:/{print $2}' /proc/meminfo)
    swfree=$(awk '/^SwapFree:/{print $2}' /proc/meminfo)
    swused=$(( (swtot - swfree) / 1024 ))

    # PSI, поле "some avg10" — частка часу, коли хоч один процес стояв
    local psimem psiio psicpu
    psimem=$(awk '/^some/{print $2}' /proc/pressure/memory | cut -d= -f2)
    psiio=$(awk  '/^some/{print $2}' /proc/pressure/io     | cut -d= -f2)
    psicpu=$(awk '/^some/{print $2}' /proc/pressure/cpu    | cut -d= -f2)

    # procs_blocked і кількість процесів у стані D — непереривний сон у ядрі.
    # Саме це показало б застрягання, якого не видно ані по CPU, ані по PSI.
    local blocked dstate
    blocked=$(awk '/^procs_blocked/{print $2}' /proc/stat)
    dstate=$(ps -eo state= | grep -c '^D' || true)

    # RSS і CPU транскрипції — і ТІЛЬКИ її, за шляхом до власного скрипта.
    # Було `comm=="python"`, тобто сума ВСІХ процесів з іменем python. Відколи
    # на VPS зʼявився app.poller (Air Alerts), поле показувало ~70 МБ навіть у
    # дні без жодної транскрипції — тобто "whisper_rss" переставав означати
    # whisper. Та сама помилка, що й у перевірці 3 сторожа (інцидент 007):
    # метрика дивилась на тінь замість власного обʼєкта.
    #
    # Через pgrep, а НЕ через `ps | awk -v pat=...`: шаблон лежить у власному
    # командному рядку awk, тому awk знаходив сам себе й малював 3 МБ "whisper"
    # на порожньому сервері. pgrep себе зі списку виключає.
    local wrss wcpu pids
    pids=$(pgrep -d, -f "$HOME/lectures/bin/transcribe_worker\.py" || true)
    if [ -n "$pids" ]; then
        read -r wrss wcpu < <(ps -o rss=,pcpu= -p "$pids" | awk '{r+=$1; c+=$2} END{printf "%d %.1f", r/1024, c+0}')
    else
        wrss=0; wcpu=0.0
    fi

    # скільки зараз живих сесій sshd
    local nssh
    # Без `|| echo 0`: воно дописало б другий нуль і розірвало б рядок лога
    # навпіл, бо sshd — останнє поле. Зараз не проявляється лише тому, що
    # майстер-процес sshd є завжди, тобто нуля не буває; але залежати від
    # цього не варто — це та сама пастка grep/pgrep -c, що й у інциденті 007.
    nssh=$(pgrep -c -f 'sshd:' || true)
    [[ "$nssh" =~ ^[0-9]+$ ]] || nssh=0

    printf '%s\tload=%s\tavail=%sM\tfree=%sM\tswap=%sM\tpsi_mem=%s\tpsi_io=%s\tpsi_cpu=%s\tblocked=%s\tD=%s\twhisper_rss=%sM\twhisper_cpu=%s\tsshd=%s\n' \
        "$now" "$l1" "$memav" "$memfree" "$swused" \
        "$psimem" "$psiio" "$psicpu" "$blocked" "$dstate" \
        "$wrss" "$wcpu" "$nssh" >> "$file"
}

sample
sleep 30
sample

find "$LOGDIR" -type f -name 'sampler-*.log' -mtime +"$RETENTION_DAYS" -delete 2>/dev/null

exit 0
