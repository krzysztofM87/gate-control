# ChatGPT handoff notes

Ten plik służy do kontynuacji projektu w nowym czacie ChatGPT. Można wkleić poniższy blok do nowej rozmowy albo wskazać plik `docs/chat-handoff.md` i `docs/server-deployment.md`.

## Krótki kontekst dla nowego czatu

Kontynuujemy projekt `gate-control`: zdalne sterowanie bramą/szlabanem przez stronę WWW i ESP32.

Repozytorium GitHub:

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

Alias SSH lokalnie:

```powershell
ssh gate-vps
```

Użytkownik roboczy na VPS:

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
  -> https://tools.malmaz.com/gate-control/brama/{token}
  -> Nginx
  -> http://127.0.0.1:8010
  -> Docker container gate-server
  -> FastAPI

ESP32
  -> GET  /gate-control/api/device/poll
  -> POST /gate-control/api/device/ack
```

Nginx obsługuje prefiks `/gate-control/` i przekazuje ruch do FastAPI. Backend FastAPI ma trasy bez prefiksu, a publiczne linki generuje przez zmienną:

```env
PUBLIC_PATH_PREFIX=/gate-control
```

## Aktualne endpointy FastAPI

```text
GET  /health
GET  /
GET  /brama/{token}
POST /brama/{token}/open
GET  /api/device/poll
POST /api/device/ack
GET  /debug/state
```

Publiczne adresy przez Nginx:

```text
GET  /gate-control/health
GET  /gate-control/
GET  /gate-control/brama/{token}
POST /gate-control/brama/{token}/open
GET  /gate-control/api/device/poll
POST /gate-control/api/device/ack
GET  /gate-control/debug/state
```

## Aktualny stan MVP

Backend działa i odpowiada na:

```bash
curl https://tools.malmaz.com/gate-control/health
```

Oczekiwana odpowiedź zawiera m.in.:

```json
{
  "status": "ok",
  "service": "gate-control",
  "public_path_prefix": "/gate-control"
}
```

MVP przechowuje komendę `open` w pamięci procesu. To znaczy:

- kliknięcie przycisku zapisuje komendę w RAM,
- ESP32 odpytuje `/api/device/poll`,
- po `/api/device/ack` komenda jest czyszczona,
- restart kontenera kasuje stan.

To jest celowe tylko na etap testów. Następny etap to SQLite.

## Ważne pliki

```text
server/app/main.py
server/Dockerfile
server/docker-compose.yml
server/requirements.txt
deploy.ps1
deploy/nginx/gate-control.conf
deploy/apply-nginx.sh
docs/server-deployment.md
docs/chat-handoff.md
```

## Deploy z VS Code / Windows

Standardowy deploy:

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

Użytkownik chce, aby zawsze proponować nazwę commita.

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

Nginx ma wystawiać aplikację wyłącznie pod:

```text
/gate-control/
```

Nie przejmować całego `tools.malmaz.com`, bo domena będzie służyć też do innych narzędzi.

## Plik `.env`

Prawdziwy `.env` jest tylko na VPS:

```text
/opt/gate-control/server/.env
```

Nie commitować `.env`.

Wymagane / używane pola:

```env
APP_ENV=production
APP_NAME=gate-control
BASE_URL=https://tools.malmaz.com
PUBLIC_PATH_PREFIX=/gate-control
DATABASE_URL=sqlite:///./data/gate-control.sqlite3
DEVICE_ID=gate-main
DEVICE_TOKEN=sekret_tylko_na_vps
COMMAND_RELAY_TIME_MS=700
TOKEN_DEFAULT_VALID_HOURS=72
OPEN_COOLDOWN_SECONDS=5
LOG_LEVEL=info
```

Token generować na VPS:

```bash
openssl rand -hex 32
```

## Przydatne komendy na VPS

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

## Typowe błędy już rozwiązane

1. `Attribute "app" not found in module "app.main"`  
   Przyczyna: `server/app/main.py` był pusty. Rozwiązanie: dodać `app = FastAPI(...)`.

2. `no configuration file provided: not found`  
   Przyczyna: `docker compose` był uruchamiany w złym katalogu albo brakowało `server/docker-compose.yml`.

3. `curl: Failed to connect to 127.0.0.1 port 8010`  
   Przyczyna: kontener nie działał albo restartował się przez błąd aplikacji.

4. Problemy PowerShell/SSH z `\r` i `unexpected end of file`  
   Rozwiązanie: `deploy.ps1` tworzy tymczasowy skrypt `.deploy-remote.sh`, wysyła go przez `scp` i uruchamia na VPS.

5. `gate-vps` nie działał  
   Przyczyna: plik `C:\Users\CP24\.ssh\config` był błędnie zapisany jako `config.txt`. Poprawiono na `config`.

## Ustalenia projektowe

- ESP32 nie ma być wystawiane do internetu.
- ESP32 odpytuje serwer, czyli model polling.
- Klient dostaje link do strony z przyciskiem.
- Samo wejście w link nie otwiera bramy.
- Dopiero kliknięcie przycisku wysyła komendę.
- Token w URL ma być docelowo losowy i czasowy, nie oparty na samym numerze rezerwacji.
- Docelowo system ma mieć logi użycia.
- Sterownik ESP32 ma zwierać styki przycisku pilota przez przekaźnik/tranzystor na ok. 0,5-1 s.
- Pilot ma dwa przyciski / dwie bramy lub szlabany, więc docelowo trzeba obsłużyć więcej niż jeden kanał.

## Następny etap techniczny

Najbliższe zadanie:

1. Wprowadzić SQLite.
2. Dodać modele/tabele:
   - devices,
   - access_tokens,
   - commands,
   - command_logs / access_logs.
3. Token klienta ma mieć:
   - losową wartość,
   - datę ważności od/do,
   - status aktywny/użyty/wygasły,
   - opcjonalnie limit użyć,
   - przypisanie do bramy/kanału.
4. Komenda `open` ma być zapisywana w bazie.
5. ESP32 przez `/api/device/poll` ma pobierać najstarszą oczekującą komendę dla swojego `DEVICE_ID`.
6. ESP32 przez `/api/device/ack` ma potwierdzać wykonanie.
7. Backend ma zapisywać logi: czas, IP, token, device, command_id, status.

## Proponowany prompt do nowego czatu

```text
Kontynuujemy projekt gate-control z repo krzysztofM87/gate-control. Przeczytaj docs/chat-handoff.md i docs/server-deployment.md. Aktualnie działa FastAPI w Dockerze na VPS pod 127.0.0.1:8010, Nginx wystawia aplikację pod https://tools.malmaz.com/gate-control/. Deploy robię z Windows/VS Code przez .\deploy.ps1 i ssh gate-vps. MVP trzyma komendę open w RAM. Następny etap: SQLite dla tokenów, komend i logów, bez wrzucania sekretów do repo. Zawsze proponuj nazwę commita.
```
