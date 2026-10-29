# 67space — віртуальний стенд мережі коворкінгу

Team-3. Цифрова інфраструктура студентського коворкінгу: сегментація мережі за ролями
(Студенти, Викладачі, Гості), captive portal і авторизація, ліміти гостьових сесій, моніторинг і журнал
підключень. Усе зроблено на віртуальній моделі (OPNsense у VirtualBox), без реального обладнання.
Результати стосуються моделі. Числові пороги, яких немає у вимогах (TBD), є припущеннями моделі.

## Склад стенда
- **VM `opnsense`** (OPNsense 26.7, 2 CPU, 2 ГБ, диск 20 ГБ): адаптер 1 NAT (WAN), адаптер 2 Host-only
  (LAN `192.168.56.10`, вебінтерфейс `https://192.168.56.10`). На LAN-інтерфейсі VLAN 10 (STUDENTS),
  20 (TEACHERS), 30 (GUESTS) з адресами `10.67.<10|20|30>.1/24`, DHCP, firewall, captive portal
  (по зоні на сегмент, сторінка входу 67space), ваучери для гостей, обмеження швидкості гостей,
  Monit (перевірка точок доступу: власний тест `67space_AP_Ping`, 2 пінги з тайм-аутом 2 с).
- **VM `alpine-client`** (Alpine Linux, 1 CPU, `192.168.56.20`): модель клієнтів і обладнання.
  - пристрої `dev-student`, `dev-teacher`, `dev-guest` (network namespace, кожен у своєму VLAN, власна MAC,
    адреса по DHCP);
  - точки доступу `ap-1`…`ap-3` (`192.168.56.31`–`.33`), їх доступність перевіряє моніторинг;
  - внутрішній сервер університету (HTTP на `192.168.56.20`);
  - до 60 клієнтів навантаження (`load_clients.sh`, лише на час тесту TC-02).
- **Панель моніторингу** (`scripts/monitor.py`) на комп'ютері адміністратора: `http://127.0.0.1:8067`.

## Політика доступу
| Сегмент | Інтернет | Внутрішні ресурси | Інші сегменти й керування | Ліміти |
|---|---|---|---|---|
| STUDENTS (VLAN 10) | так | так | ні | — |
| TEACHERS (VLAN 20) | так | так | ні | — |
| GUESTS (VLAN 30)   | так, після ваучера | ні | ні | 3 Мбіт/с на пристрій; сесія до 60 хв; без активності 15 хв |

До входу пристрій у будь-якому сегменті бачить лише сторінку порталу. Кожна зона пускає тільки акаунти
своєї групи (гостьова — лише ваучери). Після завершення гостьової сесії потрібен повторний вхід.

Припущення моделі (у вимогах TBD): поріг швидкості R-06 = 2 Мбіт/с; ліміт гостя = 3 Мбіт/с (менше за
вихід стенда в інтернет ~10–15 Мбіт/с, щоб ліміт можна було виміряти); N_peak = 60 пристроїв (30/15/15);
затримка відображення збою обладнання ≤ 60 с; строк зберігання журналів 31 день.

## Як відтворити стенд
1. **VirtualBox** (7.x). Створіть Host-only мережу (`192.168.56.1/24`, DHCP вимкнено).
2. **VM `opnsense`**: встановіть OPNsense з ISO (тип BSD / FreeBSD 64-bit). Адаптер 1 NAT, адаптер 2
   Host-only. У консолі: LAN = `em1` з адресою `192.168.56.10/24`.
3. **Ключ API**: у вебінтерфейсі System → Access → Users → root → API keys. Збережіть у
   `secrets/OPNsense.internal_root_apikey.txt` (див. `secrets/README.md`).
4. **VLAN** (API): виконайте один раз
   ```
   cd scripts
   python -c "import opn; [opn.api('/api/interfaces/vlan_settings/addItem', data={'vlan':{'if':'em1','tag':str(t),'descr':'67space '+n,'vlanif':f'vlan0.{t}'}}) for t,n in ((10,'Students'),(20,'Teachers'),(30,'Guests'))]; opn.api('/api/interfaces/vlan_settings/reconfigure', data={})"
   ```
5. **Ручні кроки у вебінтерфейсі** (в API їх немає):
   - Interfaces → Assignments: додайте `vlan0.10`, `vlan0.20`, `vlan0.30`; у кожного Enable,
     Static IPv4: опис `STUDENTS` / `TEACHERS` / `GUESTS`, адреси `10.67.10.1/24`, `10.67.20.1/24`, `10.67.30.1/24`.
   - System → Access → Servers → Add: Type `Voucher`, назва `Vouchers`, Use simple passwords, довжина 6/6.
6. **Налаштування через API** (ідемпотентно, можна запускати повторно):
   ```
   python scripts/opnsense_setup_sprint1.py     # DHCP, аліаси, правила firewall
   python scripts/opnsense_portal_sprint1.py    # групи, акаунти, зони captive portal
   python scripts/opnsense_setup_sprint2.py     # ліміти гостей, обмеження швидкості, Monit, строк журналів
   python scripts/opnsense_portal_template.py   # сторінка входу 67space
   ```
7. **VM `alpine-client`**: Alpine Linux (virtual), 1 CPU, адаптер 1 Host-only з **Promiscuous Mode: Allow All**,
   адаптер 2 NAT. Пакети й пристрої:
   ```
   apk add iproute2 curl nmap iputils tcpdump busybox-extras
   # скопіюйте scripts/client_devices.sh і scripts/load_clients.sh у /root, потім:
   sh /root/client_devices.sh            # пристрої + точки доступу (після кожного перезапуску VM)
   ```
   Для тестів потрібен доступ SSH за ключем `secrets/id_67space`.

## Моніторинг і журнал підключень
```
python scripts/monitor.py         # панель http://127.0.0.1:8067, Ctrl+C — зупинити
```
Панель оновлюється кожні 10 с: кількість підключень за сегментами, навантаження каналу (Мбіт/с на
інтерфейсах), стан точок доступу (дані Monit з OPNsense), активні сесії й останні записи журналу.
Журнал підключень `data/connections.csv` (роль, користувач, MAC, IP, початок, кінець, трафік) і журнал
подій обладнання `data/events.csv`; записи старші за 31 день видаляються. Журнал входів порталу є
також в OPNsense: Services → Captive Portal → Log File.

## Адміністрування (коротко)
- **Новий студент / викладач**: System → Access → Users → Add, група `v67-students` або `v67-teachers`.
- **Ваучери для гостей**: Services → Captive Portal → Vouchers → Create vouchers (сервер `Vouchers`,
  тривалість 1 година). Видавайте гостю логін і пароль із CSV.
- **Відключити користувача**: Services → Captive Portal → Sessions → Disconnect.
- **Точка доступу недоступна**: на панелі «НЕДОСТУПНА» (≤ 60 с; на стенді — 14–18 с навіть для всіх трьох). Перевірити живлення / кабель точки;
  у моделі: `ip netns exec ap-2 ip link set mgmt0 up` у alpine-client.
- **Панель без зв'язку з OPNsense**: червоний банер «Помилка джерела даних — немає зв'язку з OPNsense», стан точок
  «НЕВІДОМО», «Дані станом на» — час останніх успішних даних. Перевірити, чи відповідає `https://192.168.56.10`.
- **Перезапуск VM**: лише вимкнення (ACPI / `poweroff` в alpine) і новий запуск. Перезавантаження зсередини VM
  (reboot) на хості з Hyper-V валить VirtualBox (OPNsense — aborted, alpine зависає в BIOS).
- **Резервна копія**: System → Configuration → Backups → Download configuration (файл містить хеші
  паролів і ключі: зберігати як секрет). Відновлення: там само → Restore.

## Тести
```
python scripts/run_opn_tests.py               # Sprint 1: TC-04, TC-03, TC-08
python scripts/run_sprint2_tests.py           # Sprint 2: TC-05, TC-07, TC-02, TC-10 (~15 хв)
python scripts/run_sprint2_tests.py TC-07     # окремий тест
python scripts/portal_screens.py              # TC-09 (емуляція): сторінка входу на 4 типах екранів
python scripts/smoke_check.py                 # Freeze: smoke check базової версії (SC-01…SC-03), ~1 хв
python scripts/run_testing1.py                # Testing 1: TC-04, TC-03, TC-08, TC-05, TC-07, TC-02 + перевірка baseline, ~15 хв
python scripts/run_negative_tests.py          # Testing 2: negative-сценарії NT-01…NT-04 + перевірка baseline, ~8 хв
python scripts/run_retest17.py FINAL          # заняття 17: retest NT-04 + regression TC-05, TC-04, TC-08 на stable-v1.1, ~6 хв
python scripts/diag_voucher_overlap.py 10     # заняття 17: скільки секунд працюють 2 сесії одного ваучера (ISS-02)
```
Testing 1 і 2 пишуть логи в `evidence/testing1/` і `evidence/testing2/`; перевірка baseline порівнює вміст
конфігурації OPNsense з резервною копією Freeze без міток часу збереження. Перші прогони збережено в `run1/`
(`testing2/run2/` — другий прогін negative-сценаріїв).
Логи пишуться в `evidence/sprint1-opnsense/` і `evidence/sprint2-opnsense/`. Кожен запуск створює свіжі
ваучери; тест TC-07 на 2 хв скорочує тайм-аут гостьової зони й потім повертає 60 хв; TC-02 створює
тимчасові акаунти `load-*` і після себе їх видаляє.

## Базова версія (Implementation Freeze) — freeze-v1.0
- Снапшоти VirtualBox `freeze-v1.0` у VM `opnsense` і `alpine-client` (повернення: Snapshots → Restore).
- Резервна копія конфігурації OPNsense `secrets/baseline/config-freeze-v1.0.xml` (секрет, не в Git) і її SHA-256;
  smoke check перевіряє, що поточна конфігурація з нею збігається.
- `evidence/freeze/baseline-manifest.txt` — контрольні суми всіх файлів репозиторію на момент Freeze.
Після Freeze зміни вносяться лише як виправлення знайдених на Testing дефектів, з новою позначкою версії.

## Стабільна версія (заняття 17) — stable-v1.1
freeze-v1.0 + два точкові виправлення:
- ISS-01 (D-04): `scripts/monitor.py` — при втраті зв'язку з OPNsense стан точок «НЕВІДОМО», час останніх успішних даних;
- ISS-04: тест Monit `67space_AP_Ping` для AP-1…AP-3 (`scripts/opnsense_setup_sprint2.py`, функція `monit()`).
Снапшоти VirtualBox `stable-v1.1` обох VM; резервна копія `secrets/baseline/config-stable-v1.1.xml` і її SHA-256;
`evidence/retest17/baseline-v1.1-manifest.txt` — контрольні суми файлів. Логи retest — `evidence/retest17/`.

## Тестові акаунти (лише для стенда)
Студенти `student01`/`Stud01-lab`, `student02`/`Stud02-lab`; викладач `teacher01`/`Teach01-lab`;
гості входять за ваучерами (логін + пароль від OPNsense).

## Що не входить у репозиторій
`secrets/` (ключі й ваучери), `data/` (журнали), образи VM, скриншоти та Word-документи лежать на
спільному Google Drive.

## Не перевірялося
Покриття й швидкість реального Wi-Fi (R-01, R-06), радіонавантаження, доступність за місяць (R-09),
вхід на реальних iOS / Android / macOS (TC-09 — лише емуляція), інтеграція з університетськими акаунтами (R-13).
