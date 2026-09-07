#!/bin/bash
# Надсилає повідомлення в той самий телеграм-бот, що й сповіщення про вхід по SSH.
#
# Токен і chat_id лежать у ~/lectures/secrets/telegram.env (права 600),
# витягнуті з /usr/local/bin/ssh_alert.sh (root-скрипт, підвішений через
# pam_exec у /etc/pam.d/sshd). ЯКЩО ТОКЕН КОЛИСЬ МІНЯЮТЬ — оновити обидва місця.
#
# Свідомо без parse_mode: назви лекцій містять дужки, тире й крапки, і markdown
# у телеграма на них падає з 400, а повідомлення тихо зникає. Для сторожа,
# який існує саме щоб не зникати непоміченим, це неприйнятно.
#
# Кожна спроба лишає рядок у logs/notify.log з HTTP-кодом: зламаний алерт
# інакше не відрізнити від відсутності алертів.

set -u

ENV_FILE="$HOME/lectures/secrets/telegram.env"
LOG="$HOME/lectures/logs/notify.log"

TEXT="${1:-}"
[ -n "$TEXT" ] || { echo "$(date '+%F %T') ERROR порожній текст" >> "$LOG"; exit 2; }

if [ ! -r "$ENV_FILE" ]; then
    echo "$(date '+%F %T') ERROR немає $ENV_FILE" >> "$LOG"
    exit 1
fi
# shellcheck disable=SC1090
. "$ENV_FILE"

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
        -d "chat_id=$TG_CHAT_ID" \
        -d "disable_web_page_preview=1" \
        --data-urlencode "text=$TEXT" \
        "https://api.telegram.org/bot$TG_BOT_TOKEN/sendMessage")

first_line=$(printf '%s' "$TEXT" | head -1)
echo "$(date '+%F %T') http=$code | $first_line" >> "$LOG"

[ "$code" = "200" ]
