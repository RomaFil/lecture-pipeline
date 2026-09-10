#!/bin/bash
# Тест логіки watchdog.sh у пісочниці — "сторож для сторожа" + добовий дайджест.
# Досі не мав жодного тесту, хоча вже двічі був джерелом мовчазних дефектів
# (008-D: `grep -c || echo 0` друкував зайвий рядок "0" у щоденний дайджест —
# єдиний звіт, який справді читають). Той самий клас перевіряють кейси
# "перевірка 4" в alert_harness.sh — тут той самий підхід для watchdog.sh.
#
# Запуск:  bash ~/lectures/tests/test_watchdog.sh [шлях-до-watchdog.sh]
set -u
SRC="${1:-$HOME/lectures/bin/watchdog.sh}"
[ -f "$SRC" ] || { echo "немає $SRC"; exit 2; }

T="$HOME/watchdogtest"
rm -rf "$T"
mkdir -p "$T/state/alerts" "$T/bin" "$T/shim" "$T/logs/sampler" "$T/incoming" \
         "$T/outgoing" "$T/_needs-review" "$T/archive"

# Логуємо ПОВНЕ повідомлення (не лише першу стрічку) — інакше регресію 008-D
# (зайвий рядок "0" усередині тексту дайджесту) неможливо побачити взагалі.
printf '%s\n' '#!/bin/bash' \
  'echo "===FIRED===" >> "$HOME/watchdogtest/fired.log"' \
  'echo "$1" >> "$HOME/watchdogtest/fired.log"' \
  > "$T/bin/notify.sh"
chmod +x "$T/bin/notify.sh"

# той самий шим date, що й alert_harness.sh: підміняє лише годину (+%-H)
printf '%s\n' '#!/bin/bash' \
  'case "${1:-}" in' \
  '  +%-H) [ -n "${FAKE_HOUR:-}" ] && { echo "$FAKE_HOUR"; exit 0; } ;;' \
  'esac' \
  'exec /bin/date "$@"' > "$T/shim/date"
chmod +x "$T/shim/date"
export PATH="$T/shim:$PATH"

sed -e "s|^BASE=.*|BASE=\"$T\"|" "$SRC" > "$T/bin/watchdog.sh"
chmod +x "$T/bin/watchdog.sh"

PASS=0; FAIL=0
check() {
    local got="$1" want="$2" label="$3"
    if [ "$got" = "$want" ]; then PASS=$((PASS+1)); echo "  OK   $label"
    else FAIL=$((FAIL+1)); echo "  FAIL $label (want=$want got=$got)"; fi
}
sent() {
    # НЕ `grep -c ... || echo 0`: те саме, що 007/008-D — grep -c на нулі
    # збігів друкує "0" І виходить з кодом 1, тож `|| echo 0` дописав би другий
    # рядок. Санітизуємо явно, як зроблено в самому watchdog.sh.
    local n
    n=$(grep -c "$1" "$T/fired.log" 2>/dev/null || true)
    [[ "$n" =~ ^[0-9]+$ ]] || n=0
    echo "$n"
}

reset() {
    # rm -rf .../* НЕ чіпає .heartbeat (dotfile, glob його не бачить) —
    # чистимо теку явно, а не покладаємось на wildcard.
    rm -rf "$T/state/alerts"
    mkdir -p "$T/state/alerts"
    rm -f "$T/fired.log" "$T/logs"/*.log 2>/dev/null
    : > "$T/logs/process.log"
    unset FAKE_HOUR
}

echo "=== 1. живість сторожа (heartbeat) ==="

reset
touch -d "-5 minutes" "$T/state/alerts/.heartbeat"
bash "$T/bin/watchdog.sh"
check "$(sent 'МОВЧИТЬ')" 0 "свіжий heartbeat (5 хв) — мовчить"

reset
touch -d "-50 minutes" "$T/state/alerts/.heartbeat"
bash "$T/bin/watchdog.sh"
check "$(sent 'МОВЧИТЬ')" 1 "застарілий heartbeat (50 хв, поріг 45) — кричить"

reset
bash "$T/bin/watchdog.sh"
check "$(sent 'жодного разу не відзвітувався')" 1 "heartbeat відсутній узагалі — кричить"

echo "=== 2. кулдаун і відновлення ==="

reset
touch -d "-50 minutes" "$T/state/alerts/.heartbeat"
bash "$T/bin/watchdog.sh"
bash "$T/bin/watchdog.sh"
check "$(sent 'МОВЧИТЬ')" 1 "другий прогін у межах кулдауну — не дублює"

# heartbeat ожив -> штамп має знятись, і наступна застарілість має кричати
# одразу, а не чекати кулдауну з попереднього разу (clear_alert, інцидент 010)
touch -d "-1 minute" "$T/state/alerts/.heartbeat"
bash "$T/bin/watchdog.sh"
touch -d "-50 minutes" "$T/state/alerts/.heartbeat"
bash "$T/bin/watchdog.sh"
check "$(sent 'МОВЧИТЬ')" 2 "рецидив після одужання кричить одразу, не глушиться старим кулдауном"

echo "=== 3. добовий дайджест ==="

reset
FAKE_HOUR=8 bash "$T/bin/watchdog.sh"
check "$(sent 'добовий звіт')" 0 "до DIGEST_HOUR (8 < 9) — не шле"

reset
FAKE_HOUR=9 bash "$T/bin/watchdog.sh"
check "$(sent 'добовий звіт')" 1 "о DIGEST_HOUR — шле рівно раз"
FAKE_HOUR=14 bash "$T/bin/watchdog.sh"
check "$(sent 'добовий звіт')" 1 "повторний прогін того ж дня — не дублює"

echo "=== 4. регресія 008-D: порожній лог не дає зайвого '0' ==="

reset
: > "$T/logs/process.log"
FAKE_HOUR=9 bash "$T/bin/watchdog.sh"
line_after=$(grep -A1 "Лекцій в обробці" "$T/fired.log" | tail -1)
if [ "$line_after" = "0" ]; then
    FAIL=$((FAIL+1)); echo "  FAIL зайвий рядок '0' одразу після лічильника (регресія 008-D повернулась)"
else
    PASS=$((PASS+1)); echo "  OK   немає зайвого рядка '0' після лічильника"
fi
check "$(grep -c 'Лекцій в обробці за добу: 0$' "$T/fired.log")" 1 \
    "лічильник на порожньому лозі — чистий '0' в кінці рядка"

echo
echo "ПІДСУМОК: пройдено $PASS, провалено $FAIL"
[ "$FAIL" -eq 0 ]
