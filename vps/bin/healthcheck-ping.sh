#!/bin/bash
# Зовнішній dead man's switch: пінгує healthchecks.io кожні 15 хв (cron).
# Якщо пінг не прийде вчасно — healthchecks.io сам кричить у телеграм
# (@HealthchecksBot), незалежно від того, живий VPS і cron чи ні.
#
# URL лежить у ~/lectures/secrets/healthchecks.env (права 600).

set -u

ENV_FILE="$HOME/lectures/secrets/healthchecks.env"

if [ ! -r "$ENV_FILE" ]; then
    echo "$(date '+%F %T') ERROR немає $ENV_FILE" >&2
    exit 1
fi
# shellcheck disable=SC1090
. "$ENV_FILE"

curl -fsS --retry 3 --max-time 10 "$HC_PING_URL" >/dev/null
