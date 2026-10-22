# 67space — віртуальний стенд мережі коворкінгу

Team-3. Цифрова інфраструктура студентського коворкінгу: сегментація мережі за ролями
(Студенти, Викладачі, Гості), captive portal та авторизація. Усе виконано на віртуальній
моделі (OPNsense у VirtualBox), без реального обладнання. Результати стосуються моделі.

## Склад стенда
- **VM `opnsense`** (OPNsense 26.7, 2 CPU, 2 ГБ, диск 20 ГБ): адаптер 1 NAT (WAN), адаптер 2 Host-only
  (LAN `192.168.56.10`, вебінтерфейс `https://192.168.56.10`). На LAN-інтерфейсі VLAN 10 (STUDENTS),
  20 (TEACHERS), 30 (GUESTS) з адресами `10.67.<10|20|30>.1/24`, DHCP, firewall і captive portal
  (по зоні на сегмент), ваучери для гостей, плагін FreeRADIUS (встановлений, для Sprint 2).
- **VM `alpine-client`** (Alpine Linux, `192.168.56.20`): три «пристрої» (network namespace
  `dev-student`, `dev-teacher`, `dev-guest`), кожен у своєму VLAN з адресою по DHCP, а також
  змодельований внутрішній сервер університету (HTTP на `192.168.56.20`).

## Політика доступу
| Сегмент | Інтернет | Внутрішні ресурси | Інші сегменти й керування |
|---|---|---|---|
| STUDENTS (VLAN 10) | так | так | ні |
| TEACHERS (VLAN 20) | так | так | ні |
| GUESTS (VLAN 30)   | так, після ваучера (1 година) | ні | ні |

До входу пристрій у будь-якому сегменті бачить лише сторінку порталу. Кожна зона порталу пускає
тільки акаунти своєї групи.

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
   ```
   Після цього в зоні GUESTS змініть сервер автентифікації на `Vouchers` (Services → Captive Portal →
   зона GUESTS) і згенеруйте ваучери (Services → Captive Portal → Vouchers).
7. **VM `alpine-client`**: Alpine Linux (virtual), адаптер 1 Host-only з **Promiscuous Mode: Allow All**,
   адаптер 2 NAT. Встановіть пакети й запустіть пристрої:
   ```
   apk add iproute2 curl nmap iputils tcpdump busybox-extras
   sh scripts/client_devices.sh          # виконується всередині alpine-client
   ```
   Для тестів потрібен доступ SSH за ключем `secrets/id_67space`.

## Тести Sprint 1
```
python scripts/run_opn_tests.py
```
Виконує TC-04 (captive portal), TC-03 (сегментація), TC-08 (ізоляція гостей). Логи пишуться в
`evidence/sprint1-opnsense/`. Кожен запуск створює свіжий ваучер.

Останній результат: TC-04 25/25, TC-03 23/23, TC-08 9/9 (див. `evidence/sprint1-opnsense/`).

## Тестові акаунти (лише для стенда)
Студенти `student01`/`Stud01-lab`, `student02`/`Stud02-lab`; викладач `teacher01`/`Teach01-lab`;
гості входять за ваучерами (логін + пароль від OPNsense).

## Що не входить у репозиторій
- `secrets/` (ключі й ваучери), образи VM, скриншоти та Word-документи лежать на спільному Google Drive.

## Не перевірялося
Покриття й швидкість Wi-Fi, радіонавантаження, доступність за місяць, перевірка сторінки входу на
реальних iOS/Android/macOS. Для цього приймаються припущення.
