#!/bin/bash
# Гарантує, що фоновий cpulimit-монітор для llama-server живий.
#
# ollama-serve.sh стартує його один раз, при @reboot. Якщо сам монітор
# випадково впаде між перезавантаженнями VPS — CPU-захист llama-server тихо
# зникає, і ніхто про це не дізнається, доки наступний важкий тест чи
# класифікація не повторить інцидент 012 (VPN Романа просів через WireGuard).
# Тому окремий периодичний виклик, той самий гейт, що й у ollama-serve.sh
# (щоб не плодити дублі, якщо монітор і так живий).
BASE="$HOME/lectures"

command -v cpulimit >/dev/null 2>&1 || exit 0
pgrep -f 'cpulimit .*-e llama-server' >/dev/null 2>&1 && exit 0

cpulimit -l 400 -e llama-server -b >/dev/null 2>&1 &
disown
echo "$(date '+%Y-%m-%d %H:%M:%S') WARN ensure-ollama-cpulimit: монітор був мертвий, перезапущено" \
    >> "$BASE/logs/process.log"
