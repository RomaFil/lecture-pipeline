#!/bin/bash
# Сторож для сторожа + щоденний дайджест. Cron кожні 30 хв.
#
# ПРОБЛЕМА. Сторож усередині системи не може помітити власну смерть. Якщо
# alert.sh упаде або зависне, тиша виглядатиме точно як "усе добре" — найгірший
# режим відмови для системи сповіщень, бо він створює хибну впевненість.
#
# ДВА НЕЗАЛЕЖНІ МЕХАНІЗМИ:
#  1. Живість: alert.sh лишає heartbeat на кожному успішному прогоні. Якщо той
#     застарів понад HEARTBEAT_MAX_MIN — крик у телеграм.
#  2. Щоденний дайджест: позитивний сигнал о DIGEST_HOUR. Сенс не в числах, а в
#     тому, що ЙОГО ВІДСУТНІСТЬ теж стає сигналом. Тому числа мають бути
#     справді корисні — дайджест, який не читають, не працює як heartbeat.
#
# ЧОГО ЦЕ НЕ ЛОВИТЬ: смерть самого cron або вимкнений VPS — тоді мовчать обидва
# сторожі. Єдине справжнє лікування — пінгувати щось ЗОВНІ (див. lecture-pipeline).

set -u

BASE="$HOME/lectures"
STATE="$BASE/state/alerts"
NOTIFY="$BASE/bin/notify.sh"

HEARTBEAT="$STATE/.heartbeat"
HEARTBEAT_MAX_MIN=45      # alert.sh ходить кожні 15 хв; 45 = три пропущені
COOLDOWN_MIN=360
DIGEST_HOUR=9

mkdir -p "$STATE"

fire() {
    local key="$1" text="$2" stamp="$STATE/$1.stamp" age
    if [ -f "$stamp" ]; then
        age=$(( ( $(date +%s) - $(stat -c %Y "$stamp") ) / 60 ))
        [ "$age" -lt "$COOLDOWN_MIN" ] && return 0
    fi
    "$NOTIFY" "$text" && touch "$stamp"
}

# --- 1. Чи живий сторож ------------------------------------------------------
if [ -f "$HEARTBEAT" ]; then
    hb_age=$(( ( $(date +%s) - $(stat -c %Y "$HEARTBEAT") ) / 60 ))
    if [ "$hb_age" -gt "$HEARTBEAT_MAX_MIN" ]; then
        fire watchdog_dead "$(printf '%s\n' \
            "🔴 Сторож конвеєра МОВЧИТЬ" \
            "" \
            "alert.sh не відзвітувався $hb_age хв (норма — кожні 15)." \
            "Поки він мовчить, відсутність алертів нічого не означає." \
            "" \
            "Перевір: crontab -l | grep alert" \
            "         tail ~/lectures/logs/alert.log" \
            "         tail ~/lectures/logs/notify.log")"
    else
        rm -f "$STATE/watchdog_dead.stamp"
    fi
else
    fire watchdog_never "$(printf '%s\n' \
        "🔴 Сторож конвеєра жодного разу не відзвітувався" \
        "" \
        "Немає $HEARTBEAT — alert.sh не відпрацював успішно ані разу.")"
fi

# --- 2. Щоденний дайджест ----------------------------------------------------
today=$(date +%F)
digest_stamp="$STATE/digest.date"
last_digest=$(cat "$digest_stamp" 2>/dev/null || echo "")

if [ "$(date +%-H)" -ge "$DIGEST_HOUR" ] && [ "$last_digest" != "$today" ]; then

    n_in=$(find "$BASE/incoming"      -type f 2>/dev/null | wc -l)
    n_out=$(find "$BASE/outgoing"     -type f 2>/dev/null | wc -l)
    n_rev=$(find "$BASE/_needs-review" -type f 2>/dev/null | wc -l)
    n_arch=$(find "$BASE/archive"     -type f 2>/dev/null | wc -l)

    # скільки лекцій пішло в обробку за минулу добу
    yday=$(date -d 'yesterday' +%F)
    # БЕЗ `|| echo 0`: grep -c друкує "0" І виходить з кодом 1, коли збігів
    # немає, тож `|| echo 0` дописував другий нуль. У тихі дні дайджест
    # друкував "Лекцій в обробці за добу: 0" і окремий рядок "0" — тобто
    # єдиний звіт, який справді читають, щодня виглядав зіпсованим.
    # Та сама помилка була в перевірці 4 сторожа (інцидент 007).
    done_cnt=$(grep -cE "^($today|$yday) .* === .*\.(mkv|mp4)" "$BASE/logs/process.log" 2>/dev/null || true)
    [[ "$done_cnt" =~ ^[0-9]+$ ]] || done_cnt=0

    disk=$(df -h / | awk 'NR==2{print $4" вільно з "$2" ("$5" зайнято)"}')

    # мінімум вільної памʼяті за добу — зі знімків семплера
    ram_min=$(cat "$BASE"/logs/sampler/sampler-"$today".log "$BASE"/logs/sampler/sampler-"$yday".log 2>/dev/null \
              | grep -oP 'avail=\K[0-9]+' | sort -n | head -1)
    [ -n "${ram_min:-}" ] || ram_min="?"

    # --- бік ПК: зі звіту, який синк лишає на кожному прогоні ---
    PCFILE="$BASE/state/pc-status.env"
    pcval() { grep -m1 "^$1=" "$PCFILE" 2>/dev/null | cut -d= -f2- ; }

    if [ -f "$PCFILE" ]; then
        pc_age=$(( ( $(date +%s) - $(stat -c %Y "$PCFILE") ) / 60 ))
        pc_line="ПК: звіт $pc_age хв тому, диск $(pcval disk_free_gb)/$(pcval disk_total_gb) ГБ, у черзі $(pcval pending_count)"
        nw=$(pcval night_wake_ok)
        case "$nw" in
            1) wake_line="Нічне пробудження о 23:00: СПРАЦЮВАЛО" ;;
            0) wake_line="Нічне пробудження о 23:00: не спрацювало (як і очікувалось)" ;;
            *) wake_line="Нічне пробудження о 23:00: невідомо" ;;
        esac
        to=$(pcval task_timeouts_24h); fa=$(pcval task_failures_24h)
        [ "${to:-0}" -gt 0 ] 2>/dev/null && wake_line="$wake_line
Таймлімітів за добу: $to"
        [ "${fa:-0}" -gt 0 ] 2>/dev/null && wake_line="$wake_line
Невдалих запусків за добу: $fa"
    else
        pc_line="ПК: звіту ще немає"
        wake_line=""
    fi

    "$NOTIFY" "$(printf '%s\n' \
        "✅ Конвеєр лекцій — добовий звіт" \
        "" \
        "Лекцій в обробці за добу: $done_cnt" \
        "Черга: incoming=$n_in  outgoing=$n_out  needs-review=$n_rev  archive=$n_arch" \
        "Диск VPS: $disk" \
        "RAM VPS: мінімум вільної за добу ${ram_min}M" \
        "$pc_line" \
        "$wake_line" \
        "" \
        "Цей звіт приходить щодня о ${DIGEST_HOUR}:xx." \
        "Якщо він не прийшов — щось зламалось мовчки.")" && echo "$today" > "$digest_stamp"
fi

exit 0
