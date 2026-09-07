# Скопіюй у config.ps1 і підстав свої значення. config.ps1 у .gitignore.

# Куди ходити по SSH. Радимо адресу всередині VPN-тунелю, а не публічний IP:
# на публічному порту зазвичай висить fail2ban, який ріже за кілька невдалих спроб.
$VpsHost    = 'user@10.8.0.1'
$VpsPort    = '2222'   # свій нестандартний порт, не копіюй цей
$RemoteBase = '/home/user/lectures'

# Тека, куди пише OBS. Заведи окрему саме під лекції: скрипт видаляє
# локальні файли після успішної передачі, і випадкове особисте відео
# в спільній теці буде втрачено.
$WatchDir   = 'C:\Users\USERNAME\Videos\Lectures'

# Куди класти сирі транскрипти в Obsidian-сховищі
$VaultRaw   = 'C:\Users\USERNAME\Documents\obsidian\Brain\raw\lectures'

# Журнал обробки сховища. Порожній рядок — не вести журнал.
$WikiLog    = 'C:\Users\USERNAME\Documents\obsidian\Brain\wiki\wiki_log.md'

# Куди писати логи самого скрипта
$LogDir     = 'C:\Users\USERNAME\Scripts\lectures\logs'
