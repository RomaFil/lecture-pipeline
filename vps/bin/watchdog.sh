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

    to=0; fa=0; pending=0; wake_line=""
    if [ -f "$PCFILE" ]; then
        pc_age=$(( ( $(date +%s) - $(stat -c %Y "$PCFILE") ) / 60 ))
        pending=$(pcval pending_count); [[ "$pending" =~ ^[0-9]+$ ]] || pending=0
        pc_line="ПК: звіт $pc_age хв тому, диск $(pcval disk_free_gb)/$(pcval disk_total_gb) ГБ, у черзі $pending"

        # Нічне пробудження задокументовано непрацюючим апаратно (закрито
        # 09.09.2026 — див. lecture-pipeline.md). Писати "не спрацювало (як і
        # очікувалось)" щодня — привчати ігнорувати звіт заради очікуваного
        # факту, який не зміниться. Мовчати в нормі, згадувати лише коли
        # РАПТОМ спрацювало — оце й буде новина.
        nw=$(pcval night_wake_ok)
        [ "$nw" = "1" ] && wake_line="❗ Нічне пробудження о 23:00 цього разу СПРАЦЮВАЛО (документована поведінка — що воно апаратно не працює; варто перевірити, чи щось змінилось)"

        to=$(pcval task_timeouts_24h); fa=$(pcval task_failures_24h)
        [[ "$to" =~ ^[0-9]+$ ]] || to=0
        [[ "$fa" =~ ^[0-9]+$ ]] || fa=0
    else
        pc_line="ПК: звіту ще немає"
    fi

    # --- "на що звернути увагу": непорожнє лише коли є реальний сигнал ---
    # Раніше "Таймлімітів за добу: N" і "Невдалих запусків за добу: N" стояли
    # голими числами під зеленою галочкою — щоб зрозуміти, чи це "само
    # пройшло" чи "щось тихо загубилось", доводилось лізти в логи руками.
    # Тепер кожна цифра одразу пояснює, що вона означає і що з нею робити.
    attention=()
    [ "$n_rev" -gt 0 ] && attention+=("— needs-review: $n_rev файл(ів) — класифікатор не впізнав дисципліну, розібрати вручну (~/lectures/_needs-review/)")
    [ "$to" -gt 0 ]    && attention+=("— Таймлімітів за добу: $to — синк на ПК вбито за лімітом часу (30 хв), він сам перезапустився. У черзі на ПК зараз: $pending (0 означає, що файл таки дійшов)")
    [ "$fa" -gt 0 ]    && attention+=("— Невдалих запусків за добу: $fa — здебільшого той самий випадок, що й таймліміт вище")

    if [ "${#attention[@]}" -gt 0 ]; then
        header="⚠️ Конвеєр лекцій — добовий звіт (є на що глянути)"
    else
        header="✅ Конвеєр лекцій — добовий звіт"
    fi

    lines=(
        "$header"
        ""
        "Лекцій в обробці за добу: $done_cnt"
        "Черга: incoming=$n_in  outgoing=$n_out  needs-review=$n_rev  archive=$n_arch"
        "Диск VPS: $disk"
        "RAM VPS: мінімум вільної за добу ${ram_min}M"
        "$pc_line"
    )
    [ -n "$wake_line" ] && lines+=("$wake_line")
    if [ "${#attention[@]}" -gt 0 ]; then
        lines+=("" "На що звернути увагу:")
        lines+=("${attention[@]}")
    fi
    lines+=("" "Цей звіт приходить щодня о ${DIGEST_HOUR}:xx." "Якщо він не прийшов — щось зламалось мовчки.")

    "$NOTIFY" "$(printf '%s\n' "${lines[@]}")" && echo "$today" > "$digest_stamp"
fi

exit 0
