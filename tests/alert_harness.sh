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
         "$T/logs" "$T/state/alerts" "$T/bin" "$T/shim" "$T/backups"

# Перевірка 12 дивиться на теку бекапів сховища. Показуємо їй пісочницю, а не
# /var/backups: інакше тест залежав би від того, що саме зараз лежить на VPS,
# і кейс "здоровий стан" міг би падати через справжній стан бекапів.
export LECTURE_BACKUP_DIR="$T/backups"

# Умови 13-14 читають лог поза $BASE (на бойовому боксі — root:root у /var/log).
# Той самий трюк підміни, що LECTURE_BACKUP_DIR вище.
export LECTURE_UPDATE_LOG="$T/update.log"

printf '%s\n' '#!/bin/bash' \
  'echo "FIRED: $(echo "$1" | head -1)" >> "$HOME/alerttest/fired.log"' \
  'printf "%s\n=====\n" "$1" >> "$HOME/alerttest/full.log"' > "$T/bin/notify.sh"
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

# Перевірка 11 питає календар, скільки записів чекати (timetable.py expect).
# Тут — шим: друкує FAKE_EXPECT, а без нього падає, як недоступний календар.
# Дату, яку передав сторож, записує — щоб перевірити, що питають про СЬОГОДНІ.
printf '%s\n' '#!/bin/bash' \
  'echo "$1" > "$HOME/alerttest/expect.arg"' \
  '[ -n "${FAKE_EXPECT:-}" ] || exit 1' \
  'echo "$FAKE_EXPECT"' > "$T/bin/expect"
chmod +x "$T/bin/expect"
export LECTURE_EXPECT_CMD="$T/bin/expect"
mkdir -p "$T/secrets"

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
# bk <скільки> <same|vary> [годин_тому_для_найсвіжішого]
# Підробляє теку бекапів: N архівів однакового або різного розміру. Розмір
# задається реальними байтами — перевірка 12 читає саме stat -c %s.
bk() {
  local n="$1" mode="${2:-vary}" age_h="${3:-1}" i sz f
  mkdir -p "$T/backups"; rm -f "$T/backups"/*.tar.gz 2>/dev/null
  for i in $(seq 1 "$n"); do
    if [ "$mode" = "same" ]; then sz=1000; else sz=$(( 1000 + i * 7 )); fi
    f="$T/backups/obsidian_day$(printf %02d "$i").tar.gz"
    head -c "$sz" /dev/zero > "$f"
    touch -d "-$(( age_h + n - i )) hours" "$f"
  done
}
ulograw() { printf '%s\n' "$1" >> "$LECTURE_UPDATE_LOG"; }
ulog() { ulograw "$(/bin/date '+%F %T') $1"; }

reset() {
  rm -rf "$T/incoming"/* "$T/outgoing"/* "$T/archive"/* "$T/_needs-review"/* \
         "$T/state/alerts"/* "$T/fired.log" "$T/full.log" "$T/state/maintenance" \
         "$T/secrets/kpi_calendar.url" "$T/state/kpi_calendar.ics" "$T/expect.arg" \
         "$T/secrets/ajax_calendar.url" "$T/state/ajax_calendar.ics" 2>/dev/null
  : > "$T/logs/process.log"
  mkpc
  bk 6 vary 1
  # Здоровий стан за замовчуванням: свіжий result=ok, 0 пакетів — той самий
  # принцип, що mkpc()/bk() вище (reset = "усе гаразд", а не порожньо).
  : > "$LECTURE_UPDATE_LOG"
  ulog 'result=ok checked=1 busy=0 upgraded=0 pkgs="" pending_other="" reboot_required=no busy_at_reboot=no rebooted=no duration_s=30'
}
old() { touch -d "$1" "$2"; }
# rec N — N сьогоднішніх відео в archive (стан "оброблено")
rec() { local i; for i in $(seq 1 "$1"); do touch "$T/archive/aa${i}__${TODAY} 1${i}-00-00.mkv"; done; }
# trs N — N транскриптів у outgoing, тобто ТІ САМІ лекції, що вже в archive
trs() { local i; mkdir -p "$T/outgoing/Фізика"; for i in $(seq 1 "$1"); do touch "$T/outgoing/Фізика/Тема $i ($DOT) [1${i}-00].md"; done; }

pass=0; fail=0
run() {
  local desc="$1" expect="$2" key="$3" got=SILENT
  FAKE_HOUR="${H:-12}" FAKE_DOW="${D:-3}" FAKE_EXPECT="${E:-}" "$T/bin/alert.sh" >/dev/null 2>&1
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

echo "=== перевірка 1: сплячий ПК не поломка (27.09.2026) ==="
reset; old '-4 hours' "$T/outgoing/x.md"; old '-5 hours' "$T/state/pc-status.env"
                                          H=12 D=3 run "ПК мовчить 5 год, транскрипт 4 год — мовчить" SILENT outgoing_stuck
reset; old '-4 hours' "$T/outgoing/x.md"; old '-30 minutes' "$T/state/pc-status.env"
                                          H=12 D=3 run "ПК на звʼязку, транскрипт 4 год — кричить (pull падає)" FIRE outgoing_stuck
old '-5 hours' "$T/state/pc-status.env";  H=12 D=3 run "ПК заснув — штамп знято (рецидив прилетить одразу)" SILENT outgoing_stuck
reset; old '-4 hours' "$T/outgoing/x.md"; rm -f "$T/state/pc-status.env"
                                          H=12 D=3 run "звіту ПК немає зовсім — мовчить, без збою set -u" SILENT outgoing_stuck

echo "=== перевірка 11: очікувана кількість — з календаря ==="
reset;        H=21 E=4 run "4 онлайн-пари, 0 записів — кричить" FIRE no_recording
reset; rec 3; H=21 E=4 run "3 з 4 — кричить (недобір)" FIRE no_recording
reset; rec 4; H=21 E=4 run "4 з 4 — мовчить" SILENT no_recording
reset; rec 5; H=21 E=4 run "5 з 4 — мовчить" SILENT no_recording
reset; rec 1; H=21 E=1 run "1 з 1 — мовчить" SILENT no_recording
reset;        H=21 E=1 run "0 з 1 — кричить" FIRE no_recording
reset;        H=21 E=0 run "календар: онлайн-пар 0 — не день пар, мовчить" SILENT no_recording
reset;        H=21     run "календар недоступний — мовчить (кричить перевірка 16)" SILENT no_recording
reset;        H=21 E=abc run "календар повернув сміття — мовчить, не падає" SILENT no_recording
reset; rec 4; H=19 E=4 run "19:00 — ще рано, мовчить" SILENT no_recording
reset;        H=21 E=4 run "готуємо: питаємо календар" FIRE no_recording
got_day=$(cat "$T/expect.arg" 2>/dev/null)
if [ "$got_day" = "$TODAY" ]; then pass=$((pass+1)); echo "  OK   11. календар питають саме про сьогодні ($TODAY)"
else fail=$((fail+1)); echo "  FAIL 11. календар питали про [$got_day], а не $TODAY"; fi

echo "=== перевірка 11: подвійний рахунок (регресія P2) ==="
reset; rec 3; trs 3; H=21 E=4 run "3 відео + 3 транскрипти = 3, не 6" FIRE no_recording
reset; rec 4; trs 4; H=21 E=4 run "4 відео + 4 транскрипти = 4 — мовчить" SILENT no_recording
reset; rec 3; touch "$T/_needs-review/${TODAY} 17-00-00.mkv"
              H=21 E=4 run "3 + 1 у _needs-review = 4 — мовчить" SILENT no_recording
reset; rec 3; touch "$T/_needs-review/${TODAY}_17-00_abcd1234.md"
              H=21 E=4 run ".md у _needs-review не рахується" FIRE no_recording
reset; rec 3; sed -i 's/^recordings_today=.*/recordings_today=1/' "$T/state/pc-status.env"
              H=21 E=4 run "3 на VPS + 1 ще на ПК = 4 — мовчить" SILENT no_recording
reset; touch "$T/archive/aa__2026-01-01 10-00-00.mkv"
              H=21 E=4 run "є лише старе відео — кричить" FIRE no_recording
# ПК ще жодного разу не звітував: pcint має повернути порожнє, а не зламати
# перевірку під set -u
reset; rec 4; rm -f "$T/state/pc-status.env"
              H=21 E=4 run "4 записи і НЕМАЄ звіту ПК — мовчить" SILENT no_recording
reset; rm -f "$T/state/pc-status.env"
              H=21 E=4 run "0 записів і немає звіту ПК — кричить" FIRE no_recording
# ПК прислав -1 (помилка читання теки) — не має рахуватись як запис
reset; rec 4; sed -i 's/^recordings_today=.*/recordings_today=-1/' "$T/state/pc-status.env"
              H=21 E=4 run "ПК прислав -1 — не віднімає і не додає" SILENT no_recording

echo "=== решта умов (регресія) ==="
reset; touch "$T/_needs-review/z.md";      H=12 D=3 run "2. needs-review" FIRE needs_review
reset; old '-6 hours' "$T/incoming/z.mkv"; H=12 D=3 run "3. incoming стоїть" FIRE incoming_stuck
reset; old '-6 hours' "$T/incoming/z.mkv"; touch -d '+2 hours' "$T/state/maintenance"
                                           H=12 D=3 run "3. прапорець обслуговування діє — мовчить" SILENT incoming_stuck
reset; old '-6 hours' "$T/incoming/z.mkv"; touch -d '-1 hours' "$T/state/maintenance"
                                           H=12 D=3 run "3. прапорець прострочений — знову кричить" FIRE incoming_stuck
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
# Штамп має зникнути на першому ж спокійному прогоні — інакше наступна помилка
# в межах кулдауну (6 год) буде мовчки з'їдена. Саме це й сталося 09.09.2026.
                                           H=12 D=3 run "4. штамп знято, коли нових ERROR немає" SILENT process_error
/bin/date >> "$T/logs/process.log"
sed -i '$ s/$/ ERROR друга помилка/' "$T/logs/process.log"
# Тут `run` не годиться: він дивиться лише на наявність штампа, а стара
# (зламана) версія лишала штамп висіти — тобто кейс проходив би й на баговому
# коді. Питання не "чи є штамп", а "чи справді ПОЛЕТІЛО друге повідомлення",
# тому перевіряємо лічильник у підставному нотифікаторі.
FAKE_HOUR=12 FAKE_DOW=3 "$T/bin/alert.sh" >/dev/null 2>&1
sent=$(grep -c 'нові помилки обробки' "$T/fired.log" 2>/dev/null || true)
[[ "$sent" =~ ^[0-9]+$ ]] || sent=0
if [ "$sent" -eq 2 ]; then
  pass=$((pass+1)); printf '  OK   %-54s %s
' "4. друга помилка ДОЛЕТІЛА, не з'їдена кулдауном" "SENT=2"
else
  fail=$((fail+1)); printf '  FAIL %-54s очікував SENT=2, отримав SENT=%s
' "4. друга помилка ДОЛЕТІЛА, не з'їдена кулдауном" "$sent"
fi
reset; old '-10 days' "$T/archive/z.mkv";  H=12 D=3 run "5. архів не порожніє" FIRE archive_stuck
reset; old '-5 hours' "$T/state/pc-status.env"; H=12 D=3 run "6. ПК мовчить 5 год — спить, мовчить (27.09)" SILENT pc_stale
reset; old '-25 hours' "$T/state/pc-status.env"; H=12 D=3 run "6. ПК мовчить понад добу — кричить" FIRE pc_stale
reset; old '-25 hours' "$T/state/pc-status.env"; H=3  D=3 run "6. понад добу, але 03:00 — не будимо" SILENT pc_stale
reset; sed -i 's/^disk_free_gb=.*/disk_free_gb=25/' "$T/state/pc-status.env"
                                           H=12 D=3 run "7. диск 25 ГБ — кричить (поріг 30)" FIRE pc_disk
reset; sed -i 's/^disk_free_gb=.*/disk_free_gb=35/' "$T/state/pc-status.env"
                                           H=12 D=3 run "7. диск 35 ГБ — мовчить" SILENT pc_disk
reset; sed -i 's/^pending_oldest_h=.*/pending_oldest_h=8/' "$T/state/pc-status.env"
                                           H=12 D=3 run "8. записи лежать на ПК" FIRE pc_pending
reset; sed -i 's/^task_timeouts_24h=.*/task_timeouts_24h=4/' "$T/state/pc-status.env"
                                           H=12 D=3 run "9. таймліміт бʼє задачу" FIRE pc_timeouts
reset; sed -i -e 's/^on_ac=.*/on_ac=0/' -e 's/^battery_pct=.*/battery_pct=20/' "$T/state/pc-status.env"
                                           H=21 D=3 run "10. батарея перед ніччю — більше не кричить (прибрано 27.09)" SILENT pc_battery

echo "=== перевірка 16: календар КПІ ==="
reset;                                     H=12 D=3 run "16. календар не налаштований — мовчить" SILENT calendar_stale
reset; touch "$T/secrets/kpi_calendar.url"; H=12 D=3 run "16. адреса є, кешу немає — кричить" FIRE calendar_stale
reset; touch "$T/secrets/kpi_calendar.url" "$T/state/kpi_calendar.ics"
                                           H=12 D=3 run "16. свіжий кеш — мовчить" SILENT calendar_stale
reset; touch "$T/secrets/kpi_calendar.url"; old '-50 hours' "$T/state/kpi_calendar.ics"
                                           H=12 D=3 run "16. кеш 50 год — кричить" FIRE calendar_stale
touch "$T/state/kpi_calendar.ics";         H=12 D=3 run "16. кеш ожив — штамп знято" SILENT calendar_stale
reset;                                     H=12 D=3 run "16a. календар Ajax не налаштований — мовчить" SILENT calendar_ajax_stale
reset; touch "$T/secrets/ajax_calendar.url"; H=12 D=3 run "16a. адреса Ajax є, кешу немає — кричить" FIRE calendar_ajax_stale
reset; touch "$T/secrets/ajax_calendar.url"; old '-50 hours' "$T/state/ajax_calendar.ics"
                                           H=12 D=3 run "16a. кеш Ajax 50 год — кричить" FIRE calendar_ajax_stale
touch "$T/state/ajax_calendar.ics";        H=12 D=3 run "16a. кеш Ajax ожив — штамп знято" SILENT calendar_ajax_stale
reset; touch "$T/secrets/ajax_calendar.url"; H=12 D=3 run "16a. застарілий Ajax не будить алерт КПІ" SILENT calendar_stale

echo "=== перевірка 12: бекап сховища ==="
reset; bk 6 vary 1;   H=12 D=3 run "12. архіви свіжі й різні — мовчить" SILENT backup_stale
reset; bk 5 same 1;   H=12 D=3 run "12. 5 однакових розмірів — кричить" FIRE backup_stale
reset; bk 4 same 1;   H=12 D=3 run "12. 4 однакових — ще в межах норми" SILENT backup_stale
reset; bk 6 vary 48;  H=12 D=3 run "12. найсвіжіший архів 48 год тому — кричить" FIRE backup_stale
reset; bk 5 same 1;   H=12 D=3 run "12. готуємо штамп" FIRE backup_stale
bk 6 vary 1;          H=12 D=3 run "12. джерело ожило — штамп знято" SILENT backup_stale
reset; rm -rf "$T/backups"
                      H=12 D=3 run "12. теки бекапів немає — мовчить, це не збій" SILENT backup_stale
mkdir -p "$T/backups"

echo "=== перевірка 13: нічний апдейт — помітний результат ==="
reset; ulog 'result=skipped_busy checked=1 busy=1 upgraded=0 pkgs="" pending_other="" reboot_required=no busy_at_reboot=no rebooted=no duration_s=1'
             H=12 D=3 run "13. skipped_busy — кричить" FIRE nightly_update
reset; ulog 'result=ok checked=1 busy=0 upgraded=0 pkgs="" pending_other="" reboot_required=no busy_at_reboot=no rebooted=no duration_s=40'
             H=12 D=3 run "13. ok, 0 пакетів — мовчить" SILENT nightly_update
reset; ulog 'result=ok checked=1 busy=0 upgraded=3 pkgs="libc6,perl,tzdata" pending_other="" reboot_required=no busy_at_reboot=no rebooted=no duration_s=40'
             H=12 D=3 run "13. ok, 3 пакети — кричить" FIRE nightly_update
reset; ulog 'result=ok checked=1 busy=0 upgraded=1 pkgs="linux-image-generic" pending_other="" reboot_required=yes busy_at_reboot=no rebooted=yes duration_s=90'
             H=12 D=3 run "13. reboot_required=yes — кричить" FIRE nightly_update
reset; ulog 'result=apt_error stage=update checked=1 busy=0 upgraded=0 pkgs="" pending_other="" reboot_required=unknown busy_at_reboot=no rebooted=no duration_s=2'
             H=12 D=3 run "13. apt_error — кричить" FIRE nightly_update
reset; ulog 'post_reboot_check services_ok=no failed="wg-quick@wg0"'
             H=12 D=3 run "13. services_ok=no після ребуту — кричить" FIRE nightly_update
reset; ulog 'result=ok checked=1 busy=0 upgraded=0 pkgs="" pending_other="docker-ce,docker-ce-cli" reboot_required=no busy_at_reboot=no rebooted=no duration_s=38'
             H=12 D=3 run "13. лише pending_other (docker-ce) — мовчить, навмисно (Роман, 10.09.2026)" SILENT nightly_update
reset; ulog 'result=ok checked=1 busy=0 upgraded=2 pkgs="libc6,perl" pending_other="" reboot_required=no busy_at_reboot=no rebooted=no duration_s=40'
             H=12 D=3 run "13. готуємо штамп" FIRE nightly_update
             H=12 D=3 run "13. без нового рядка в лозі — штамп знято" SILENT nightly_update

echo "=== перевірка 14: нічний апдейт мовчить понад місяць ==="
reset; : > "$LECTURE_UPDATE_LOG"
       ulograw "$(/bin/date -d '-5 days' '+%F %T') result=ok checked=1 busy=0 upgraded=0 pkgs=\"\" pending_other=\"\" reboot_required=no busy_at_reboot=no rebooted=no duration_s=40"
             H=12 D=3 run "14. останній ok 5 днів тому — мовчить" SILENT nightly_update_stale
reset; : > "$LECTURE_UPDATE_LOG"
       for i in $(seq 1 8); do ulograw "$(/bin/date -d "-$((40-i)) days" '+%F %T') result=skipped_busy checked=1 busy=1 upgraded=0 pkgs=\"\" pending_other=\"\" reboot_required=no busy_at_reboot=no rebooted=no duration_s=1"; done
             H=12 D=3 run "14. 40 днів тиші, здебільшого skipped_busy — кричить" FIRE nightly_update_stale
reset; : > "$LECTURE_UPDATE_LOG"
       for i in $(seq 1 8); do ulograw "$(/bin/date -d "-$((40-i)) days" '+%F %T') result=apt_error stage=update checked=1 busy=0 upgraded=0 pkgs=\"\" pending_other=\"\" reboot_required=unknown busy_at_reboot=no rebooted=no duration_s=2"; done
             H=12 D=3 run "14. 40 днів тиші, apt_error — кричить" FIRE nightly_update_stale
reset; : > "$LECTURE_UPDATE_LOG"
             H=12 D=3 run "14. лог порожній (root-крон ще не ходив) — кричить" FIRE nightly_update_stale
# Реальний випадок 10.09.2026: годину по встановленню, перший прогін —
# skipped_busy (черга ще не розібрана), жодного result=ok ще не було. Стара
# версія рахувала stale_days=999999 і кричала "мовчить понад 30 днів" — брехня,
# логу кілька годин. Правильно: мовчати, поки не мине сам поріг.
reset; : > "$LECTURE_UPDATE_LOG"
       ulog 'result=skipped_busy checked=1 busy=1 upgraded=0 pkgs="" pending_other="" reboot_required=no busy_at_reboot=no rebooted=no duration_s=0'
             H=12 D=3 run "14. лише 1 skipped_busy СЬОГОДНІ, ще нема ok — мовчить (не 30 днів)" SILENT nightly_update_stale
reset; : > "$LECTURE_UPDATE_LOG"
       ulograw "$(/bin/date -d '-35 days' '+%F %T') result=skipped_busy checked=1 busy=1 upgraded=0 pkgs=\"\" pending_other=\"\" reboot_required=no busy_at_reboot=no rebooted=no duration_s=0"
             H=12 D=3 run "14. 1 skipped_busy, але 35 днів тому і відтоді жодного ok — кричить" FIRE nightly_update_stale
reset; rm -f "$LECTURE_UPDATE_LOG"
             H=12 D=3 run "14. лог узагалі не існує — теж кричить, не мовчить як зламана перевірка" FIRE nightly_update_stale

echo "=== перевірка 15: розклад втрутився в класифікацію ==="
HINT1='WARNING SCHEDULE_HINT: розклад [ФІЗИКА] переважив більшість ТЕОРІЯ_КІЛ 2/3 | запис: 2026-09-22 10-26-00.mkv | голоси: [ФІЗИКА, ТЕОРІЯ_КІЛ, ТЕОРІЯ_КІЛ]'
HINT2='WARNING SCHEDULE_HINT: голоси МАТАНАЛІЗ 2/3 суперечать розкладу [ТЕОРІЯ_КІЛ], жодного голосу за розклад | запис: 2026-09-22 08-45-00.mkv'
reset; "$T/bin/alert.sh" >/dev/null 2>&1
cnt=$(cat "$T/state/alerts/schedule_hint.count")
if [ "$cnt" = "0" ]; then pass=$((pass+1)); echo "  OK   15. база на порожньому лозі — один рядок 0"
else fail=$((fail+1)); echo "  FAIL 15. база зіпсована: [$cnt]"; fi
# Перший запуск лише запам'ятовує базу: історичні події — не новина
reset; printf '%s\n' "2026-09-01 10:00:00,000 $HINT1" >> "$T/logs/process.log"
                                           H=12 D=3 run "15. історичний рядок на першому запуску — мовчить" SILENT schedule_hint
printf '%s\n' "2026-09-22 10:30:00,000 $HINT1" >> "$T/logs/process.log"
                                           H=12 D=3 run "15. нова подія SCHEDULE_HINT — кричить" FIRE schedule_hint
                                           H=12 D=3 run "15. штамп знято, коли нових подій немає" SILENT schedule_hint
printf '%s\n' "2026-09-22 11:30:00,000 $HINT2" >> "$T/logs/process.log"
FAKE_HOUR=12 FAKE_DOW=3 "$T/bin/alert.sh" >/dev/null 2>&1
sent=$(grep -c 'розклад втрутився' "$T/fired.log" 2>/dev/null || true)
[[ "$sent" =~ ^[0-9]+$ ]] || sent=0
if [ "$sent" -eq 2 ]; then pass=$((pass+1)); echo "  OK   15. друга подія ДОЛЕТІЛА, не з'їдена кулдауном  SENT=2"
else fail=$((fail+1)); echo "  FAIL 15. друга подія: очікував SENT=2, отримав SENT=$sent"; fi
# У повідомленні має бути сам запис і причина — без цього алерт не каже, куди дивитись
# (фрази беруться з ДИНАМІЧНОГО рядка події: статичне пояснення в кінці алерту
# теж містить «суперечать розкладу», і перевірка за ним була б тавтологією)
# (fired.log має лише перший рядок повідомлення — повний текст у full.log)
if grep -q 'жодного голосу за розклад |' "$T/full.log" && grep -q '08-45-00.mkv' "$T/full.log"; then
  pass=$((pass+1)); echo "  OK   15. повідомлення містить причину й імʼя запису"
else fail=$((fail+1)); echo "  FAIL 15. у повідомленні немає причини чи імені запису"; fi
reset; "$T/bin/alert.sh" >/dev/null 2>&1
printf '%s\n' "2026-09-22 10:30:00,000 WARNING 2026-09-22 08-45-00.mkv: UNRECOGNIZED (голоси розійшлися)" >> "$T/logs/process.log"
                                           H=12 D=3 run "15. звичайний WARNING без маркера не рахується" SILENT schedule_hint

echo "=== перевірка 17: транскрипт з вадами (QUALITY_WARN) ==="
Q1='WARNING QUALITY_WARN: дірки 07:00-30:00 (23 хв зі звуком без тексту) | запис: 2026-09-29 14-15-00.mkv'
Q2='WARNING QUALITY_WARN: петлі 11:12×9 | запис: 2026-09-30 10-25-00.mkv'
reset; "$T/bin/alert.sh" >/dev/null 2>&1
cnt=$(cat "$T/state/alerts/quality_warn.count")
if [ "$cnt" = "0" ]; then pass=$((pass+1)); echo "  OK   17. база на порожньому лозі — один рядок 0"
else fail=$((fail+1)); echo "  FAIL 17. база зіпсована: [$cnt]"; fi
reset; printf '%s\n' "2026-09-01 10:00:00,000 $Q1" >> "$T/logs/process.log"
                                           H=12 D=3 run "17. історичний рядок на першому запуску — мовчить" SILENT quality_warn
printf '%s\n' "2026-09-29 16:30:00,000 $Q1" >> "$T/logs/process.log"
                                           H=12 D=3 run "17. нова подія QUALITY_WARN — кричить" FIRE quality_warn
                                           H=12 D=3 run "17. штамп знято, коли нових подій немає" SILENT quality_warn
printf '%s\n' "2026-09-30 12:30:00,000 $Q2" >> "$T/logs/process.log"
FAKE_HOUR=12 FAKE_DOW=3 "$T/bin/alert.sh" >/dev/null 2>&1
sent=$(grep -c 'транскрипт з вадами' "$T/fired.log" 2>/dev/null || true)
[[ "$sent" =~ ^[0-9]+$ ]] || sent=0
if [ "$sent" -eq 2 ]; then pass=$((pass+1)); echo "  OK   17. друга подія ДОЛЕТІЛА, не з'їдена кулдауном  SENT=2"
else fail=$((fail+1)); echo "  FAIL 17. друга подія: очікував SENT=2, отримав SENT=$sent"; fi
if grep -q 'петлі 11:12×9 | запис: 2026-09-30 10-25-00.mkv' "$T/full.log"; then
  pass=$((pass+1)); echo "  OK   17. повідомлення містить ваду й імʼя запису"
else fail=$((fail+1)); echo "  FAIL 17. у повідомленні немає вади чи імені запису"; fi
reset; "$T/bin/alert.sh" >/dev/null 2>&1
printf '%s\n' "2026-09-29 16:30:00,000 INFO QUALITY: {\"words\": 9000, \"hole_minutes\": 0}" >> "$T/logs/process.log"
printf '%s\n' "2026-09-29 16:30:00,000 WARNING QUALITY: оцінка не вдалась, обробка йде далі: x" >> "$T/logs/process.log"
                                           H=12 D=3 run "17. звичайний QUALITY-рядок і збій оцінки не рахуються" SILENT quality_warn

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
