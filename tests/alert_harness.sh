#!/bin/bash
# Тест логіки alert.sh у пісочниці. Доставка в телеграм доведена окремо —
# тут перевіряється ТІЛЬКИ логіка: що спрацювало саме те, що треба, і що на
# здоровому стані сторож мовчить.
#
# Запуск:  bash ~/lectures/tests/alert_harness.sh [шлях-до-alert.sh]
# За замовчуванням тестується БОЙОВИЙ ~/lectures/bin/alert.sh.
#
# Повторювати не лише після правки порогів, а й після будь-якої зміни на VPS
# поза конвеєром: інцидент 007 показав, що сусідні сервіси можуть тихо вбити
# перевірку, яку колись "перевірили спрацьованою".
set -u
SRC="${1:-$HOME/lectures/bin/alert.sh}"
[ -f "$SRC" ] || { echo "немає $SRC"; exit 2; }

T="$HOME/alerttest"
rm -rf "$T"
mkdir -p "$T/incoming" "$T/outgoing" "$T/archive" "$T/_needs-review" \
         "$T/logs" "$T/state/alerts" "$T/bin" "$T/shim"

printf '%s\n' '#!/bin/bash' \
  'echo "FIRED: $(echo "$1" | head -1)" >> "$HOME/alerttest/fired.log"' > "$T/bin/notify.sh"
chmod +x "$T/bin/notify.sh"

# шим date: підміняє годину (+%-H) і день тижня (+%u), решту віддає системному
printf '%s\n' '#!/bin/bash' \
  'case "${1:-}" in' \
  '  +%-H) [ -n "${FAKE_HOUR:-}" ] && { echo "$FAKE_HOUR"; exit 0; } ;;' \
  '  +%u)  [ -n "${FAKE_DOW:-}" ]  && { echo "$FAKE_DOW";  exit 0; } ;;' \
  'esac' \
  'exec /bin/date "$@"' > "$T/shim/date"
chmod +x "$T/shim/date"
export PATH="$T/shim:$PATH"

sed -e "s|^BASE=.*|BASE=\"$T\"|" -e "s|^NOTIFY=.*|NOTIFY=\"$T/bin/notify.sh\"|" \
    "$SRC" > "$T/bin/alert.sh"
chmod +x "$T/bin/alert.sh"

TODAY=$(/bin/date +%F)
DOT=$(/bin/date +%d.%m.%Y)

mkpc() {
  printf '%s\n' \
    "disk_free_gb=500" "disk_total_gb=930" "pending_count=0" \
    "pending_oldest_h=0" "pending_names=" "recordings_today=0" \
    "task_timeouts_24h=0" "on_ac=1" "battery_pct=90" > "$T/state/pc-status.env"
}
reset() {
  rm -rf "$T/incoming"/* "$T/outgoing"/* "$T/archive"/* "$T/_needs-review"/* \
         "$T/state/alerts"/* "$T/fired.log" 2>/dev/null
  : > "$T/logs/process.log"
  mkpc
}
old() { touch -d "$1" "$2"; }
# rec N — N сьогоднішніх відео в archive (стан "оброблено")
rec() { local i; for i in $(seq 1 "$1"); do touch "$T/archive/aa${i}__${TODAY} 1${i}-00-00.mkv"; done; }
# trs N — N транскриптів у outgoing, тобто ТІ САМІ лекції, що вже в archive
trs() { local i; mkdir -p "$T/outgoing/Фізика"; for i in $(seq 1 "$1"); do touch "$T/outgoing/Фізика/Тема $i ($DOT) [1${i}-00].md"; done; }

pass=0; fail=0
run() {
  local desc="$1" expect="$2" key="$3" got=SILENT
  FAKE_HOUR="${H:-12}" FAKE_DOW="${D:-3}" "$T/bin/alert.sh" >/dev/null 2>&1
  [ -f "$T/state/alerts/$key.stamp" ] && got=FIRE
  if [ "$got" = "$expect" ]; then
    pass=$((pass+1)); printf '  OK   %-54s %s\n' "$desc" "$got"
  else
    fail=$((fail+1)); printf '  FAIL %-54s очікував %s, отримав %s\n' "$desc" "$expect" "$got"
  fi
}

echo "=== перевірка 1: нічне вікно ==="
reset; old '-4 hours' "$T/outgoing/x.md"; H=4  D=3 run "транскрипт 4 год, 04:00 — мовчить" SILENT outgoing_stuck
reset; old '-4 hours' "$T/outgoing/x.md"; H=9  D=3 run "транскрипт 4 год, 09:00 — ще мовчить" SILENT outgoing_stuck
reset; old '-4 hours' "$T/outgoing/x.md"; H=10 D=3 run "транскрипт 4 год, 10:00 — кричить" FIRE outgoing_stuck
reset; old '-4 hours' "$T/outgoing/x.md"; H=23 D=3 run "транскрипт 4 год, 23:00 — мовчить" SILENT outgoing_stuck
reset; old '-1 hours' "$T/outgoing/x.md"; H=12 D=3 run "транскрипт 1 год — мовчить (поріг)" SILENT outgoing_stuck
reset; old '-4 hours' "$T/outgoing/x.md"; H=12 D=3 run "готуємо штамп" FIRE outgoing_stuck
rm -f "$T/outgoing/x.md";                 H=4  D=3 run "умова зникла вночі — штамп знято" SILENT outgoing_stuck

echo "=== перевірка 11: очікувана кількість (P2) ==="
reset;        H=21 D=4 run "ЧТ, 0 з 4 — кричить" FIRE no_recording
reset; rec 3; H=21 D=4 run "ЧТ, 3 з 4 — кричить (недобір)" FIRE no_recording
reset; rec 4; H=21 D=4 run "ЧТ, 4 з 4 — мовчить" SILENT no_recording
reset; rec 5; H=21 D=4 run "ЧТ, 5 з 4 — мовчить" SILENT no_recording
reset; rec 1; H=21 D=2 run "ВТ, 1 з 1 — мовчить" SILENT no_recording
reset;        H=21 D=2 run "ВТ, 0 з 1 — кричить" FIRE no_recording
reset; rec 1; H=21 D=5 run "ПТ, 1 з 1 — мовчить" SILENT no_recording
reset; rec 1; H=21 D=6 run "СБ, 1 з 2 — кричить (недобір)" FIRE no_recording
reset; rec 2; H=21 D=6 run "СБ, 2 з 2 — мовчить" SILENT no_recording
reset;        H=21 D=1 run "ПН — не день пар, мовчить" SILENT no_recording
# СР перестала бути порожнім днем 09.09.2026: бокс переїхав із спортзалу 24 в
# онлайн і тепер пишеться. Кейс не видалений, а перевернутий — саме він упав
# першим прогоном після правки LECTURE_SCHEDULE, і це правильна поведінка тесту.
reset; rec 1; H=21 D=3 run "СР, 1 з 1 (бокс) — мовчить" SILENT no_recording
reset;        H=21 D=3 run "СР, 0 з 1 (бокс) — кричить" FIRE no_recording
reset;        H=21 D=7 run "НД — не день пар, мовчить" SILENT no_recording
reset; rec 4; H=19 D=4 run "ЧТ 19:00 — ще рано, мовчить" SILENT no_recording

echo "=== перевірка 11: подвійний рахунок (регресія P2) ==="
reset; rec 3; trs 3; H=21 D=4 run "ЧТ, 3 відео + 3 транскрипти = 3, не 6" FIRE no_recording
reset; rec 4; trs 4; H=21 D=4 run "ЧТ, 4 відео + 4 транскрипти = 4 — мовчить" SILENT no_recording
reset; rec 3; touch "$T/_needs-review/${TODAY} 17-00-00.mkv"
              H=21 D=4 run "ЧТ, 3 + 1 у _needs-review = 4 — мовчить" SILENT no_recording
reset; rec 3; touch "$T/_needs-review/${TODAY}_17-00_abcd1234.md"
              H=21 D=4 run "ЧТ, .md у _needs-review не рахується" FIRE no_recording
reset; rec 3; sed -i 's/^recordings_today=.*/recordings_today=1/' "$T/state/pc-status.env"
              H=21 D=4 run "ЧТ, 3 на VPS + 1 ще на ПК = 4 — мовчить" SILENT no_recording
reset; touch "$T/archive/aa__2026-01-01 10-00-00.mkv"
              H=21 D=4 run "ЧТ, є лише вчорашнє відео — кричить" FIRE no_recording
# ПК ще жодного разу не звітував: pcint має повернути порожнє, а не зламати
# перевірку під set -u
reset; rec 4; rm -f "$T/state/pc-status.env"
              H=21 D=4 run "ЧТ, 4 записи і НЕМАЄ звіту ПК — мовчить" SILENT no_recording
reset; rm -f "$T/state/pc-status.env"
              H=21 D=4 run "ЧТ, 0 записів і немає звіту ПК — кричить" FIRE no_recording
# ПК прислав -1 (помилка читання теки) — не має рахуватись як запис
reset; rec 4; sed -i 's/^recordings_today=.*/recordings_today=-1/' "$T/state/pc-status.env"
              H=21 D=4 run "ЧТ, ПК прислав -1 — не віднімає і не додає" SILENT no_recording

echo "=== решта умов (регресія) ==="
reset; touch "$T/_needs-review/z.md";      H=12 D=3 run "2. needs-review" FIRE needs_review
reset; old '-6 hours' "$T/incoming/z.mkv"; H=12 D=3 run "3. incoming стоїть" FIRE incoming_stuck
reset; old '-6 hours' "$T/incoming/z.mkv"
echo 'import time; time.sleep(45)' > "$T/bin/process.py"
python3 "$T/bin/process.py" & PB=$!
sleep 1;                                   H=12 D=3 run "3. whisper крутиться — мовчить" SILENT incoming_stuck
kill $PB 2>/dev/null; rm -f "$T/bin/process.py"
reset; "$T/bin/alert.sh" >/dev/null 2>&1
cnt=$(cat "$T/state/alerts/process_errors.count")
if [ "$cnt" = "0" ]; then pass=$((pass+1)); echo "  OK   4. база на порожньому лозі — один рядок 0"
else fail=$((fail+1)); echo "  FAIL 4. база зіпсована: [$cnt]"; fi
/bin/date >> "$T/logs/process.log"
sed -i '$ s/$/ ERROR тест/' "$T/logs/process.log"
                                           H=12 D=3 run "4. новий ERROR" FIRE process_error
reset; old '-10 days' "$T/archive/z.mkv";  H=12 D=3 run "5. архів не порожніє" FIRE archive_stuck
reset; old '-5 hours' "$T/state/pc-status.env"; H=12 D=3 run "6. ПК мовчить удень" FIRE pc_stale
reset; old '-5 hours' "$T/state/pc-status.env"; H=3  D=3 run "6. ПК мовчить уночі — норма" SILENT pc_stale
reset; sed -i 's/^disk_free_gb=.*/disk_free_gb=25/' "$T/state/pc-status.env"
                                           H=12 D=3 run "7. диск 25 ГБ — кричить (поріг 30)" FIRE pc_disk
reset; sed -i 's/^disk_free_gb=.*/disk_free_gb=35/' "$T/state/pc-status.env"
                                           H=12 D=3 run "7. диск 35 ГБ — мовчить" SILENT pc_disk
reset; sed -i 's/^pending_oldest_h=.*/pending_oldest_h=8/' "$T/state/pc-status.env"
                                           H=12 D=3 run "8. записи лежать на ПК" FIRE pc_pending
reset; sed -i 's/^task_timeouts_24h=.*/task_timeouts_24h=4/' "$T/state/pc-status.env"
                                           H=12 D=3 run "9. таймліміт бʼє задачу" FIRE pc_timeouts
reset; sed -i -e 's/^on_ac=.*/on_ac=0/' -e 's/^battery_pct=.*/battery_pct=20/' "$T/state/pc-status.env"
                                           H=21 D=3 run "10. батарея перед ніччю" FIRE pc_battery
reset; sed -i -e 's/^on_ac=.*/on_ac=0/' -e 's/^battery_pct=.*/battery_pct=20/' "$T/state/pc-status.env"
                                           H=12 D=3 run "10. батарея вдень — мовчить" SILENT pc_battery

echo "=== здоровий стан ==="
reset
FAKE_HOUR=12 FAKE_DOW=3 "$T/bin/alert.sh" >/dev/null 2>&1
n=$(ls "$T/state/alerts"/*.stamp 2>/dev/null | wc -l)
if [ "$n" -eq 0 ]; then pass=$((pass+1)); echo "  OK   жодного алерту на здоровому стані"
else fail=$((fail+1)); echo "  FAIL спрацювало: $(ls "$T/state/alerts"/*.stamp)"; fi
if [ -f "$T/state/alerts/.heartbeat" ]; then pass=$((pass+1)); echo "  OK   heartbeat поставлено"
else fail=$((fail+1)); echo "  FAIL heartbeat відсутній"; fi

echo
echo "ПІДСУМОК: пройдено $pass, провалено $fail"
[ "$fail" -eq 0 ]
