# secrets/ (не потрапляє в Git)

Ця папка локальна для кожного учасника. Створіть тут свої файли:

| Файл | Що це | Як отримати |
|---|---|---|
| `OPNsense.internal_root_apikey.txt` | ключ і секрет API (`key=...`, `secret=...`) | OPNsense → System → Access → Users → root → API keys → `+` |
| `id_67space`, `id_67space.pub` | SSH-ключ для VM `alpine-client` | `ssh-keygen -t ed25519 -N "" -f secrets/id_67space`, публічну частину додати в `/root/.ssh/authorized_keys` на VM |

Нікому не передавайте ці файли й не вставляйте їх у чат.
