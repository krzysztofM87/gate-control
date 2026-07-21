# Server deployment notes

Ten plik opisuje aktualny stan konfiguracji VPS, Nginx, Docker Compose i aplikacji serwerowej projektu `gate-control`.

Nie zapisywać tutaj sekretów, tokenów, haseł ani kluczy prywatnych. Sekrety mają być wyłącznie w pliku `.env` na VPS.

## Repozytorium

- Repozytorium: `krzysztofM87/gate-control`
- Gałąź główna: `main`
- Lokalny katalog roboczy na Windows: `C:\dev\gate-control`
- Katalog projektu na VPS: `/opt/gate-control`

## Dostęp SSH

Lokalny alias SSH:

```powershell
ssh gate-vps
```

Użytkownik roboczy na VPS:

```text
deploy
```

Użytkownik `deploy` ma mieć dostęp do `sudo` oraz do grupy `docker`.

Sprawdzenie:

```bash
whoami
sudo whoami
groups deploy
```

## Struktura projektu na VPS

```text
/opt/gate-control/
├── deploy/
│   ├── apply-nginx.sh
│   └── nginx/
│       └── gate-control.conf
├── docs/
├── firmware/
└── server/
    ├── app/
    │   └── main.py
    ├── data/
    ├── .env
    ├── Dockerfile
    ├── docker-compose.yml
    └── requirements.txt
```

## Backend FastAPI

Backend działa w kontenerze Docker pod nazwą:

```text
gate-server
```

Mapowanie portów:

```text
127.0.0.1:8010 -> 8000/tcp w kontenerze
```

Aplikacja wewnątrz kontenera startuje przez:

```text
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Lokalny test na VPS:

```bash
curl http://127.0.0.1:8010
curl http://127.0.0.1:8010/health
```

## Docker Compose

Plik Compose znajduje się w:

```text
server/docker-compose.yml
```

Uruchamianie ręczne na VPS:

```bash
cd /opt/gate-control/server
docker compose up -d --build
```

Podgląd kontenerów:

```bash
docker ps
docker compose ps
```

Logi aplikacji:

```bash
docker logs -f gate-server
docker logs --tail=100 gate-server
```

Restart backendu:

```bash
cd /opt/gate-control/server
docker compose restart
```

Zatrzymanie backendu:

```bash
cd /opt/gate-control/server
docker compose down
```

## Plik `.env` na VPS

Prawdziwy plik `.env` znajduje się tylko na VPS:

```text
/opt/gate-control/server/.env
```

Nie commitować tego pliku.

Przykładowe pola bez sekretów:

```env
APP_ENV=production
APP_NAME=gate-control
BASE_URL=https://tools.malmaz.com
PUBLIC_PATH_PREFIX=/gate-control

DATABASE_URL=sqlite:///./data/gate-control.sqlite3

DEVICE_ID=gate-main
DEVICE_TOKEN=TU_WSTAW_DLUGI_TOKEN_TYLKO_NA_VPS
COMMAND_RELAY_TIME_MS=700
COMMAND_PENDING_TIMEOUT_SECONDS=15
TOKEN_DEFAULT_VALID_HOURS=72
OPEN_COOLDOWN_SECONDS=5

LOG_LEVEL=info
```

Generowanie tokenu na VPS:

```bash
openssl rand -hex 32
```

Po zmianie `.env` trzeba przebudować albo zrestartować kontener:

```bash
cd /opt/gate-control/server
docker compose up -d --build
```

## Publiczny adres aplikacji

Aplikacja jest wystawiana pod prefiksem:

```text
https://tools.malmaz.com/gate-control/
```

Endpoint health:

```text
https://tools.malmaz.com/gate-control/health
```

Strona testowa klienta:

```text
https://tools.malmaz.com/gate-control/brama/test-token
```

API dla urządzenia:

```text
https://tools.malmaz.com/gate-control/api/device/poll
https://tools.malmaz.com/gate-control/api/device/ack
```

## Nginx

Konfiguracja Nginx w repo:

```text
deploy/nginx/gate-control.conf
```

Docelowa konfiguracja na VPS:

```text
/etc/nginx/sites-available/gate-control
```

Włączenie konfiguracji:

```bash
sudo ln -sf /etc/nginx/sites-available/gate-control /etc/nginx/sites-enabled/gate-control
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Pomocniczy skrypt z repo:

```bash
cd /opt/gate-control
bash deploy/apply-nginx.sh
```

Test przez Nginx na VPS:

```bash
curl -H "Host: tools.malmaz.com" http://127.0.0.1/gate-control/health
```

Test publiczny:

```bash
curl -L https://tools.malmaz.com/gate-control/health
```

## Aktualny model Nginx

Nginx przyjmuje ruch z:

```text
/gate-control/
```

i przekazuje go do:

```text
http://127.0.0.1:8010/
```

Prefiks `/gate-control` jest zdejmowany przez Nginx. Backend FastAPI ma trasy bez prefiksu, np. `/health`, `/brama/{token}`, `/api/device/poll`.

W aplikacji FastAPI ustawione jest:

```text
PUBLIC_PATH_PREFIX=/gate-control
```

Dzięki temu linki w HTML są generowane jako publiczne adresy pod `/gate-control`.

## Deploy z Windows / VS Code

Standardowy deploy lokalnie:

```powershell
cd C:\dev\gate-control
.\deploy.ps1
```

Skrypt robi:

1. `git status`
2. `git add .`
3. `git commit`
4. `git push`
5. SSH na VPS
6. `git pull`
7. `docker compose up -d --build` w `/opt/gate-control/server`

Przy każdej zmianie wpisywać czytelną nazwę commita.

## Przydatne nazwy commitów

- `Add server deployment notes`
- `Add server Docker Compose deployment`
- `Add Nginx deployment config`
- `Serve gate app under gate-control path`
- `Update FastAPI routes for gate-control path prefix`
- `Fix gate server container startup`

## Aktualne testy po deployu

Na VPS:

```bash
cd /opt/gate-control/server
docker ps
curl http://127.0.0.1:8010/health
curl -H "Host: tools.malmaz.com" http://127.0.0.1/gate-control/health
```

Publicznie:

```bash
curl -L https://tools.malmaz.com/gate-control/health
```

Oczekiwana odpowiedź:

```json
{
  "status": "ok",
  "service": "gate-control",
  "public_path_prefix": "/gate-control"
}
```

## MVP logiki bramy

Aktualnie backend MVP trzyma komendę w pamięci procesu.

Przepływ:

1. Klient otwiera `/gate-control/brama/{token}`.
2. Klient klika „Otwórz bramę”.
3. Backend zapisuje komendę `open` w pamięci.
4. ESP32 odpytuje `/gate-control/api/device/poll`.
5. ESP32 wykonuje impuls przekaźnika.
6. ESP32 potwierdza `/gate-control/api/device/ack`.
7. Backend czyści oczekującą komendę.

Ograniczenie obecnego MVP:

- restart kontenera kasuje stan,
- tokeny klientów nie są jeszcze walidowane z bazy,
- brak trwałych logów użycia,
- endpoint `/debug/state` jest tymczasowy i docelowo trzeba go usunąć albo zabezpieczyć.

## Następny etap

Do zrobienia w kolejnych krokach:

1. SQLite dla tokenów, komend i logów.
2. Walidacja tokenów czasowych klienta.
3. Panel albo komenda administracyjna do generowania linków.
4. Bezpieczne nagłówki autoryzacji dla ESP32.
5. Logowanie użycia: IP, czas, token, status, potwierdzenie ESP32.
6. Usunięcie albo zabezpieczenie `/debug/state`.
