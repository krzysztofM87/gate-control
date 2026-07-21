# ChatGPT handoff notes

Ten plik sluzy do szybkiego przekazania kontekstu projektu `gate-control` do nowego czatu albo innego narzedzia. Ma byc aktualizowany po istotnych zmianach w architekturze, deployu, endpointach, firmware albo procedurze pracy.

Ostatnia aktualizacja: 2026-07-21, po lokalnej naprawie zwracania utworzonej komendy przez `create_command_from_token()`. Ostatni deploy na VPS: commit `d788070` (`Improve pilot creation form`).

## Krotki kontekst

Kontynuujemy projekt `gate-control`: zdalne sterowanie brama/szlabanem przez strone WWW oraz sterownik ESP32. ESP32 nie jest wystawione do internetu. Urzadzenie odpytuje backend, a klient dostaje link do strony-pilota.

Repozytorium:

```text
krzysztofM87/gate-control
```

Lokalny katalog na Windows:

```text
C:\dev\gate-control
```

VPS:

```text
/opt/gate-control
```

Alias SSH:

```powershell
ssh gate-vps
```

Uzytkownik roboczy na VPS:

```text
deploy
```

Publiczny adres aplikacji:

```text
https://tools.malmaz.com/gate-control/
```

Backend lokalnie na VPS:

```text
http://127.0.0.1:8010
```

Kontener Docker:

```text
gate-server
```

## Aktualna architektura

```text
Klient WWW
  -> https://tools.malmaz.com/gate-control/pilot/{token}
  -> Nginx
  -> http://127.0.0.1:8010
  -> Docker container gate-server
  -> FastAPI + SQLite

ESP32
  -> HTTP GET  /gate-control/api/device/poll
  -> HTTP POST /gate-control/api/device/ack
```

Nginx obsluguje prefiks `/gate-control/` i przekazuje ruch do FastAPI. Backend ma trasy bez prefiksu, a publiczne linki generuje przez:

```env
PUBLIC_PATH_PREFIX=/gate-control
BASE_URL=https://tools.malmaz.com
```

Firmware ESP32 w aktualnym stabilnym trybie obsluguje tylko zwykle `http://`, bez TLS. Dlatego Nginx zostawia HTTP dla `/gate-control/api/device/` bez wymuszania przekierowania na HTTPS.

## Aktualny stan aplikacji

Backend nie jest juz prostym MVP z komenda w RAM. Aktualnie uzywa SQLite i SQLAlchemy.

Modele/tabele:

```text
devices
access_tokens
commands
command_logs
```

Glowny przeplyw:

1. Admin tworzy urzadzenie i token/link.
2. Klient otwiera `/gate-control/pilot/{token}`.
3. Samo wejscie w link nie otwiera bramy.
4. Klikniecie przycisku pilota tworzy komende w SQLite ze statusem `pending`.
5. ESP32 odpytuje `/api/device/poll`.
6. Backend zwraca najstarsza komende `pending` dla danego `device_id` i ustawia jej status `sent`.
7. ESP32 zwiera odpowiednie wyjscie GPIO przez zadany czas.
8. ESP32 potwierdza `/api/device/ack`.
9. Backend zapisuje ACK i logi zdarzen.

Token klienta ma losowa wartosc, waznosc od/do albo tryb bezterminowy, status, limit uzyc, cooldown oraz przypisanie do urzadzenia i kanalu bramy.

Panel admina przy tworzeniu pilota wybiera urzadzenie z listy urzadzen z bazy. Aktywne urzadzenia sa wybieralne, wylaczone sa pokazane jako niedostepne. Typ pilota wybiera sie jako:

```text
1 przycisk - brama 1 / GPIO26
1 przycisk - brama 2 / GPIO27
3 przyciski - brama 1, brama 2, obie
```

## Aktualne endpointy

Publiczne / klient:

```text
GET  /
GET  /health
GET  /brama/{token}
POST /brama/{token}/open
POST /brama/{token}/open/{gate}
GET  /pilot/{token}
POST /pilot/{token}/press/{gate}
GET  /pilot/{token}/command/{command_id}/status
```

ESP32:

```text
GET  /api/device/poll
POST /api/device/ack
```

Admin API:

```text
POST /admin/devices
GET  /admin/devices
POST /admin/tokens
GET  /admin/tokens
GET  /admin/commands
POST /admin/tokens/delete-all
```

Panel admina HTML:

```text
GET  /admin-panel
POST /admin-panel/login
POST /admin-panel/logout
POST /admin-panel/tokens
POST /admin-panel/tokens/delete-all
GET  /admin-panel/devices
POST /admin-panel/devices
GET  /admin-panel/devices/{device_id}/edit
POST /admin-panel/devices/{device_id}/update
POST /admin-panel/devices/{device_id}/toggle
GET  /admin-panel/devices/{device_id}/delete
POST /admin-panel/devices/{device_id}/delete
```

Tymczasowe / techniczne:

```text
GET /debug/state
```

`/debug/state` nadal jest publiczne i docelowo trzeba je usunac albo zabezpieczyc.

## Wazne pliki

```text
server/app/main.py
server/app/config.py
server/app/schemas.py
server/app/services.py
server/app/views.py
server/app/models.py
server/app/database.py
server/app/routes/public.py
server/app/routes/device.py
server/app/routes/admin.py
server/app/routes/admin_panel.py
server/Dockerfile
server/docker-compose.yml
server/requirements.txt
firmware/esp32_gate/esp32_gate.ino
firmware/esp32_gate/src/api_client.cpp
firmware/esp32_gate/src/gate_control.cpp
firmware/esp32_gate/include/config.h
deploy.ps1
deploy/nginx/gate-control.conf
deploy/apply-nginx.sh
scripts/gate-admin.ps1
docs/server-deployment.md
docs/chat-handoff.md
```

Aktualny podzial backendu:

```text
server/app/main.py              - tworzy FastAPI app, startup i include_router
server/app/config.py            - zmienne srodowiskowe i stale konfiguracyjne
server/app/schemas.py           - modele Pydantic dla requestow
server/app/services.py          - logika wspolna: auth, tokeny, komendy, migracje
server/app/views.py             - helpery HTML/panelu/pilota
server/app/routes/public.py     - health, index, stary /brama, nowy /pilot, debug/state
server/app/routes/device.py     - poll/ack dla ESP32
server/app/routes/admin.py      - Admin API JSON
server/app/routes/admin_panel.py - panel admina HTML
```

## Deploy

Standardowy deploy robimy z Windows przez:

```powershell
cd C:\dev\gate-control
.\deploy.ps1 "Nazwa commita"
```

Skrypt `deploy.ps1` robi:

1. `git status`
2. `git add .`
3. `git commit -m "..."`
4. `git push`
5. `scp` tymczasowego skryptu Bash na `gate-vps`
6. SSH na VPS
7. `cd /opt/gate-control/server`
8. `git pull`
9. `docker compose up -d --build`

Wazne:

- Zawsze przed deployem sprawdzic `git status`.
- Zawsze zaproponowac czytelna nazwe commita.
- `git add .` lapie wszystko, wiec nie zostawiac przypadkowych plikow w repo.
- Skrypt nie aplikuje Nginx i nie wykonuje automatycznego healthchecka po deployu.

Ostatni testowy deploy:

```text
Commit: 48c8375 Test deploy
Data: 2026-07-21
Wynik: kontener gate-server przebudowany i uruchomiony
Healthcheck: https://tools.malmaz.com/gate-control/health -> status ok
```

## Nginx

Konfiguracja Nginx jest w repo:

```text
deploy/nginx/gate-control.conf
```

Zastosowanie na VPS:

```bash
cd /opt/gate-control
bash deploy/apply-nginx.sh
```

Nginx ma wystawiac aplikacje pod:

```text
/gate-control/
```

Nie przejmowac calego `tools.malmaz.com`, bo domena moze sluzyc tez do innych narzedzi.

## Plik .env

Prawdziwy `.env` jest tylko na VPS:

```text
/opt/gate-control/server/.env
```

Nie commitowac `.env`.

Uzywane pola:

```env
APP_ENV=production
APP_NAME=gate-control
BASE_URL=https://tools.malmaz.com
PUBLIC_PATH_PREFIX=/gate-control
DATABASE_URL=sqlite:///./data/gate-control.sqlite3
DEVICE_ID=gate-main
DEVICE_TOKEN=sekret_tylko_na_vps
DEVICE_SECRET=opcjonalnie_alias_dla_DEVICE_TOKEN
ADMIN_TOKEN=sekret_admina_tylko_na_vps
COMMAND_RELAY_TIME_MS=700
TOKEN_DEFAULT_VALID_HOURS=72
OPEN_COOLDOWN_SECONDS=5
APP_TIMEZONE=Europe/Warsaw
LOG_LEVEL=info
```

Token/sekret generowac na VPS, np.:

```bash
openssl rand -hex 32
```

## Firmware ESP32

Firmware jest w:

```text
firmware/esp32_gate
```

Piny:

```text
GPIO2  - dioda debug
GPIO26 - wyjscie brama/przycisk 1
GPIO27 - wyjscie brama/przycisk 2
GPIO0  - BOOT, wejscie do konfiguracji
```

Konfiguracja urzadzenia:

- portal AP `GateConfig-*`,
- terminal przez Serial,
- dane zapisane w Preferences/NVS,
- wymagane pola: WiFi SSID, WiFi password, server URL, device id, device secret.

Przyklad konfiguracji przez terminal:

```text
wifi NAZWA_WIFI|HASLO_WIFI
server http://tools.malmaz.com/gate-control
device gate-main|SEKRET_URZADZENIA
save
reboot
```

Firmware aktualnie korzysta z HTTP i naglowkow:

```text
X-Device-Id
X-Device-Secret
```

Aktualna lokalna konfiguracja po testowym deployu ma `LOG_LEVEL` ustawiony na `LOG_LEVEL_INFO`.

## Przydatne komendy

Na VPS:

```bash
cd /opt/gate-control/server
docker ps
docker compose ps
docker logs -f gate-server
docker logs --tail=100 gate-server
docker compose up -d --build
docker compose restart
```

Test backendu lokalnie na VPS:

```bash
curl http://127.0.0.1:8010/health
```

Test przez Nginx lokalnie na VPS:

```bash
curl -H "Host: tools.malmaz.com" http://127.0.0.1/gate-control/health
```

Test publiczny:

```bash
curl -L https://tools.malmaz.com/gate-control/health
```

Pomocniczy skrypt admina z Windows:

```powershell
.\scripts\gate-admin.ps1 -Action debug
.\scripts\gate-admin.ps1 -Action new-token -GateTarget open_1 -Label "test"
.\scripts\gate-admin.ps1 -Action poll
```

Sekrety dla `gate-admin.ps1` przekazywac przez zmienne srodowiskowe:

```powershell
$env:GATE_ADMIN_TOKEN = "..."
$env:GATE_DEVICE_SECRET = "..."
```

## Znane ryzyka / dlug techniczny

- `/debug/state` jest publiczne.
- Panel admina i Admin API pokazuja pelne tokeny oraz sekrety urzadzen.
- Brak automatycznych testow.
- Brak Alembica; migracje sa proste i reczne w `run_schema_migrations()`.
- Podzial backendu na moduly jest juz zrobiony lokalnie, ale przed deployem trzeba przejsc runtime test w srodowisku z FastAPI/SQLAlchemy.
- W odpowiedziach JSON sa drobne powtorzenia klucza `valid_forever`.
- Formularz usuwania tokenow ma jeden hardcoded path `/gate-control/admin-panel/tokens/delete-all`.
- Firmware nie obsluguje HTTPS.

## Ustalenia projektowe

- ESP32 nie ma byc wystawiane do internetu.
- Model komunikacji to polling z ESP32 do serwera.
- Samo wejscie w link nie otwiera bramy.
- Dopiero klikniecie przycisku wysyla komende.
- Token w URL ma byc losowy i czasowy albo bezterminowy, zaleznie od ustawien.
- System ma zapisywac logi uzycia.
- Sterownik ESP32 zwiera styki pilota przez przekaznik/transoptor/tranzystor na ok. 0,5-1 s.
- Pilot ma obslugiwac dwa kanaly oraz opcje obu kanalow.
- Do deployu uzywamy `deploy.ps1`.
- Ten plik `docs/chat-handoff.md` ma byc utrzymywany na biezaco.

## Najblizsze sensowne kroki

1. Zabezpieczyc albo usunac `/debug/state`.
2. Dodac healthcheck po `deploy.ps1`.
3. Dodac minimalne testy backendu dla token -> command -> poll -> ack.
4. Ograniczyc ekspozycje sekretow w panelu/admin API.
5. Docelowo dodac HTTPS w firmware albo inny bezpieczny wariant komunikacji urzadzenia.

## Prompt do nowego czatu

```text
Kontynuujemy projekt gate-control z repo krzysztofM87/gate-control. Przeczytaj docs/chat-handoff.md i docs/server-deployment.md. Aplikacja FastAPI dziala w Dockerze na VPS pod 127.0.0.1:8010, Nginx wystawia ja pod https://tools.malmaz.com/gate-control/. Backend uzywa SQLite/SQLAlchemy dla devices, access_tokens, commands i command_logs. Lokalnie main.py zostal podzielony na config/schemas/services/views/routes. Klient uzywa strony /pilot/{token}; ESP32 polluje /api/device/poll i potwierdza /api/device/ack. Deploy robimy z Windows przez .\deploy.ps1 i ssh gate-vps. Ostatni testowy deploy: commit 48c8375 Test deploy, healthcheck status ok. Zawsze sprawdzaj git status, proponuj nazwe commita i dbaj, zeby docs/chat-handoff.md byl aktualny.
```
