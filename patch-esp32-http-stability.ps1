$ErrorActionPreference = "Stop"

function Write-Utf8NoBom {
    param(
        [string]$Path,
        [string]$Content
    )

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content.TrimStart([char]0xFEFF), $utf8NoBom)
}

function Backup-File {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        throw "File not found: $Path"
    }

    $backup = "$Path.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item $Path $backup
    Write-Host "Backup: $backup"
}

$configPath = "firmware\esp32_gate\include\config.h"
$apiPath = "firmware\esp32_gate\src\api_client.cpp"

Backup-File $configPath
Backup-File $apiPath

$configContent = @'
#pragma once

#include <Arduino.h>

// ===== Pins =====
#define DEBUG_LED_PIN 2
#define BOOT_BUTTON_PIN 0

#define GATE1_OUTPUT_PIN 26
#define GATE2_OUTPUT_PIN 27

// ===== Gate relay =====
#define GATE_ACTIVE_HIGH 1
#define DEFAULT_GATE_PULSE_MS 700
#define MIN_GATE_PULSE_MS 100
#define MAX_GATE_PULSE_MS 5000

// ===== WiFi =====
#define WIFI_CONNECT_TIMEOUT_MS 20000
#define WIFI_RECONNECT_MIN_INTERVAL_MS 15000
#define WIFI_RSSI_WARN_DBM -75

// ===== API =====
#define POLL_INTERVAL_MS 5000
#define API_TIMEOUT_MS 10000
#define HTTP_RETRY_COUNT 2
#define HTTP_RETRY_DELAY_MS 400

#define API_FAILS_BEFORE_WIFI_RECONNECT 5
#define API_FAILS_BEFORE_REBOOT 60
#define STATUS_PRINT_INTERVAL_MS 60000

// ===== Power save =====
// For gate controller stability, WiFi sleep is disabled.
// CPU can stay at 80 MHz.
#define WIFI_POWER_SAVE_ENABLED 0
#define CPU_POWER_SAVE_ENABLED 1
#define CPU_FREQUENCY_MHZ 80

// ===== Config portal =====
#define CONFIG_NAMESPACE "gatecfg"
#define CONFIG_AP_PREFIX "GateConfig-"
#define CONFIG_AP_PASSWORD "12345678"
#define CONFIG_PORTAL_DNS_PORT 53

// ===== Logging =====
#define LOG_LEVEL_NONE 0
#define LOG_LEVEL_ERROR 1
#define LOG_LEVEL_WARN 2
#define LOG_LEVEL_INFO 3
#define LOG_LEVEL_DEBUG 4

#define LOG_LEVEL LOG_LEVEL_DEBUG
'@

$apiContent = @'
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <string.h>
#include <stdlib.h>

#include "../include/config.h"
#include "../include/api_client.h"
#include "../include/gate_control.h"

static char apiHost[96] = {0};
static uint16_t apiPort = 80;
static char apiBasePath[160] = {0};
static char apiDeviceId[96] = {0};
static char apiDeviceSecret[160] = {0};

static void safeCopy(char *dst, size_t dstSize, const char *src) {
  if (dstSize == 0) {
    return;
  }

  if (src == nullptr) {
    dst[0] = '\0';
    return;
  }

  strncpy(dst, src, dstSize - 1);
  dst[dstSize - 1] = '\0';
}

static bool parseServerUrl(const char *url) {
  if (url == nullptr) {
    return false;
  }

  const char *prefix = "http://";
  size_t prefixLen = strlen(prefix);

  if (strncmp(url, prefix, prefixLen) != 0) {
    if (LOG_LEVEL >= LOG_LEVEL_ERROR) {
      Serial.println("[API] Only http:// is supported in stable mode");
    }
    return false;
  }

  const char *start = url + prefixLen;
  const char *slash = strchr(start, '/');
  const char *colon = strchr(start, ':');

  size_t hostLen = 0;

  if (colon != nullptr && (slash == nullptr || colon < slash)) {
    hostLen = colon - start;
    apiPort = (uint16_t)atoi(colon + 1);
    if (apiPort == 0) {
      apiPort = 80;
    }
  } else {
    apiPort = 80;
    hostLen = slash ? (size_t)(slash - start) : strlen(start);
  }

  if (hostLen == 0 || hostLen >= sizeof(apiHost)) {
    if (LOG_LEVEL >= LOG_LEVEL_ERROR) {
      Serial.println("[API] Invalid host length");
    }
    return false;
  }

  memcpy(apiHost, start, hostLen);
  apiHost[hostLen] = '\0';

  if (slash != nullptr) {
    safeCopy(apiBasePath, sizeof(apiBasePath), slash);

    size_t len = strlen(apiBasePath);
    while (len > 0 && apiBasePath[len - 1] == '/') {
      apiBasePath[len - 1] = '\0';
      len--;
    }
  } else {
    apiBasePath[0] = '\0';
  }

  return true;
}

static bool readHttpLine(WiFiClient &client, char *line, size_t lineSize, unsigned long timeoutMs) {
  size_t pos = 0;
  unsigned long start = millis();
  unsigned long lastActivity = start;

  if (lineSize == 0) {
    return false;
  }

  line[0] = '\0';

  while (millis() - start < timeoutMs) {
    while (client.available()) {
      char c = (char)client.read();
      lastActivity = millis();

      if (c == '\r') {
        continue;
      }

      if (c == '\n') {
        line[pos] = '\0';
        return true;
      }

      if (pos < lineSize - 1) {
        line[pos++] = c;
        line[pos] = '\0';
      }
    }

    if (!client.connected() && !client.available()) {
      if (pos > 0) {
        line[pos] = '\0';
        return true;
      }

      if (millis() - lastActivity > 600) {
        break;
      }
    }

    delay(5);
  }

  line[pos] = '\0';
  return pos > 0;
}

static int parseStatusCode(const char *statusLine) {
  const char *space1 = strchr(statusLine, ' ');

  if (space1 == nullptr) {
    return -1;
  }

  return atoi(space1 + 1);
}

static bool rawHttpRequest(
  const char *method,
  const char *path,
  const char *body,
  int &httpCode,
  char *payload,
  size_t payloadSize
) {
  httpCode = 0;

  if (payloadSize > 0) {
    payload[0] = '\0';
  }

  WiFiClient client;
  client.setTimeout(API_TIMEOUT_MS);

  if (LOG_LEVEL >= LOG_LEVEL_DEBUG) {
    Serial.print("[HTTP] Connect ");
    Serial.print(apiHost);
    Serial.print(":");
    Serial.println(apiPort);
  }

  IPAddress remoteIp;
  bool dnsOk = WiFi.hostByName(apiHost, remoteIp);

  if (LOG_LEVEL >= LOG_LEVEL_DEBUG) {
    Serial.print("[HTTP] DNS ");
    Serial.print(apiHost);
    Serial.print(" -> ");

    if (dnsOk) {
      Serial.println(remoteIp);
    } else {
      Serial.println("FAILED");
    }
  }

  bool connected = false;

  if (dnsOk) {
    connected = client.connect(remoteIp, apiPort);
  } else {
    connected = client.connect(apiHost, apiPort);
  }

  if (!connected) {
    if (LOG_LEVEL >= LOG_LEVEL_WARN) {
      Serial.println("[HTTP] Connect failed");
    }

    httpCode = -11;
    client.stop();
    return false;
  }

  client.print(method);
  client.print(" ");
  client.print(path);
  client.println(" HTTP/1.1");

  client.print("Host: ");
  client.println(apiHost);

  client.println("User-Agent: ESP32-Gate/1.0");
  client.println("Accept: application/json");

  client.print("X-Device-Id: ");
  client.println(apiDeviceId);

  client.print("X-Device-Secret: ");
  client.println(apiDeviceSecret);

  client.println("Cache-Control: no-cache");
  client.println("Connection: close");

  if (strcmp(method, "POST") == 0) {
    client.println("Content-Type: application/json");
    client.print("Content-Length: ");
    client.println(strlen(body));
  }

  client.println();

  if (strcmp(method, "POST") == 0) {
    client.print(body);
  }

  char line[256];

  if (!readHttpLine(client, line, sizeof(line), API_TIMEOUT_MS)) {
    if (LOG_LEVEL >= LOG_LEVEL_WARN) {
      Serial.println("[HTTP] No status line");
    }

    httpCode = -12;
    client.stop();
    return false;
  }

  if (LOG_LEVEL >= LOG_LEVEL_DEBUG) {
    Serial.print("[HTTP] Status line: ");
    Serial.println(line);
  }

  httpCode = parseStatusCode(line);

  while (readHttpLine(client, line, sizeof(line), API_TIMEOUT_MS)) {
    if (line[0] == '\0') {
      break;
    }
  }

  size_t pos = 0;
  unsigned long start = millis();

  while (millis() - start < API_TIMEOUT_MS) {
    while (client.available()) {
      char c = (char)client.read();

      if (pos < payloadSize - 1) {
        payload[pos++] = c;
      }
    }

    if (!client.connected() && !client.available()) {
      break;
    }

    delay(5);
  }

  if (payloadSize > 0) {
    payload[pos] = '\0';
  }

  client.stop();
  delay(80);

  return httpCode >= 100;
}

static bool extractJsonString(const char *json, const char *key, char *out, size_t outSize) {
  if (outSize == 0) {
    return false;
  }

  out[0] = '\0';

  char pattern[64];
  snprintf(pattern, sizeof(pattern), "\"%s\"", key);

  const char *keyPos = strstr(json, pattern);
  if (keyPos == nullptr) {
    return false;
  }

  const char *colon = strchr(keyPos + strlen(pattern), ':');
  if (colon == nullptr) {
    return false;
  }

  const char *firstQuote = strchr(colon + 1, '"');
  if (firstQuote == nullptr) {
    return false;
  }

  const char *secondQuote = strchr(firstQuote + 1, '"');
  if (secondQuote == nullptr) {
    return false;
  }

  size_t len = secondQuote - firstQuote - 1;

  if (len >= outSize) {
    len = outSize - 1;
  }

  memcpy(out, firstQuote + 1, len);
  out[len] = '\0';

  return true;
}

static int extractJsonInt(const char *json, const char *key, int defaultValue) {
  char pattern[64];
  snprintf(pattern, sizeof(pattern), "\"%s\"", key);

  const char *keyPos = strstr(json, pattern);
  if (keyPos == nullptr) {
    return defaultValue;
  }

  const char *colon = strchr(keyPos + strlen(pattern), ':');
  if (colon == nullptr) {
    return defaultValue;
  }

  return atoi(colon + 1);
}

static uint8_t targetFromCommand(const char *command) {
  if (strcmp(command, "open_1") == 0) {
    return GATE_TARGET_1;
  }

  if (strcmp(command, "open_2") == 0) {
    return GATE_TARGET_2;
  }

  if (strcmp(command, "open_both") == 0) {
    return GATE_TARGET_BOTH;
  }

  if (strcmp(command, "open") == 0) {
    if (LOG_LEVEL >= LOG_LEVEL_WARN) {
      Serial.println("[API] Command 'open' without target, defaulting to gate 1");
    }
    return GATE_TARGET_1;
  }

  return GATE_TARGET_NONE;
}

static void buildPollPath(char *path, size_t pathSize) {
  snprintf(
    path,
    pathSize,
    "%s/api/device/poll?device_id=%s",
    apiBasePath,
    apiDeviceId
  );
}

static void buildAckPath(char *path, size_t pathSize) {
  snprintf(
    path,
    pathSize,
    "%s/api/device/ack",
    apiBasePath
  );
}

void setupApiClient(const DeviceConfig &config) {
  safeCopy(apiDeviceId, sizeof(apiDeviceId), config.deviceId.c_str());
  safeCopy(apiDeviceSecret, sizeof(apiDeviceSecret), config.deviceSecret.c_str());

  String serverUrl = config.serverUrl;
  serverUrl.trim();

  while (serverUrl.endsWith("/")) {
    serverUrl.remove(serverUrl.length() - 1);
  }

  if (LOG_LEVEL >= LOG_LEVEL_INFO) {
    Serial.print("[API] Server URL: ");
    Serial.println(serverUrl);
  }

  if (!parseServerUrl(serverUrl.c_str())) {
    if (LOG_LEVEL >= LOG_LEVEL_ERROR) {
      Serial.println("[API] Server URL parse failed");
    }
  }

  if (LOG_LEVEL >= LOG_LEVEL_INFO) {
    Serial.print("[API] Host: ");
    Serial.println(apiHost);

    Serial.print("[API] Port: ");
    Serial.println(apiPort);

    Serial.print("[API] Base path: ");
    Serial.println(apiBasePath);
  }
}

GateCommand pollGateCommand() {
  GateCommand result;

  if (WiFi.status() != WL_CONNECTED) {
    result.httpCode = -1;

    if (LOG_LEVEL >= LOG_LEVEL_WARN) {
      Serial.println("[API] WiFi not connected");
    }

    return result;
  }

  char path[240];
  char payload[1024];

  buildPollPath(path, sizeof(path));

  if (LOG_LEVEL >= LOG_LEVEL_DEBUG) {
    Serial.print("[API] Poll ");
    Serial.println(path);
  }

  int httpCode = 0;
  bool requestOk = false;

  for (uint8_t attempt = 0; attempt <= HTTP_RETRY_COUNT; attempt++) {
    payload[0] = '\0';
    httpCode = 0;

    requestOk = rawHttpRequest("GET", path, "", httpCode, payload, sizeof(payload));

    if (requestOk && httpCode >= 200 && httpCode < 500) {
      break;
    }

    if (attempt < HTTP_RETRY_COUNT) {
      if (LOG_LEVEL >= LOG_LEVEL_WARN) {
        Serial.print("[API] Poll transient error, retry ");
        Serial.print(attempt + 1);
        Serial.print("/");
        Serial.println(HTTP_RETRY_COUNT);
      }

      delay(HTTP_RETRY_DELAY_MS);
    }
  }

  result.httpCode = httpCode;

  if (LOG_LEVEL >= LOG_LEVEL_DEBUG) {
    Serial.print("[API] HTTP ");
    Serial.println(httpCode);
  }

  if (payload[0] != '\0' && LOG_LEVEL >= LOG_LEVEL_DEBUG) {
    Serial.print("[API] Payload: ");
    Serial.println(payload);
  }

  if (httpCode < 200 || httpCode >= 300) {
    if (LOG_LEVEL >= LOG_LEVEL_WARN) {
      Serial.print("[API] Non-2xx response, HTTP ");
      Serial.println(httpCode);
    }

    return result;
  }

  char command[32];

  if (!extractJsonString(payload, "command", command, sizeof(command))) {
    if (LOG_LEVEL >= LOG_LEVEL_WARN) {
      Serial.println("[API] Missing command field");
    }

    return result;
  }

  if (strcmp(command, "none") == 0) {
    if (LOG_LEVEL >= LOG_LEVEL_DEBUG) {
      Serial.println("[API] No command");
    }

    return result;
  }

  if (LOG_LEVEL >= LOG_LEVEL_INFO) {
    Serial.print("[API] Command received: ");
    Serial.println(command);
  }

  uint8_t target = targetFromCommand(command);

  if (target == GATE_TARGET_NONE) {
    if (LOG_LEVEL >= LOG_LEVEL_WARN) {
      Serial.println("[API] Unknown command");
    }

    return result;
  }

  result.shouldOpen = true;
  result.target = target;

  extractJsonString(payload, "command_id", result.commandId, sizeof(result.commandId));

  int relayMs = extractJsonInt(payload, "relay_time_ms", DEFAULT_GATE_PULSE_MS);

  if (relayMs < MIN_GATE_PULSE_MS) {
    relayMs = MIN_GATE_PULSE_MS;
  }

  if (relayMs > MAX_GATE_PULSE_MS) {
    relayMs = MAX_GATE_PULSE_MS;
  }

  result.relayTimeMs = relayMs;

  if (LOG_LEVEL >= LOG_LEVEL_INFO) {
    Serial.print("[API] Command target: ");
    Serial.println(result.target);

    Serial.print("[API] Command ID: ");
    Serial.println(result.commandId);

    Serial.print("[API] Relay ms: ");
    Serial.println(result.relayTimeMs);
  }

  return result;
}

bool ackGateCommand(const char *commandId, const char *status) {
  if (commandId == nullptr || commandId[0] == '\0') {
    if (LOG_LEVEL >= LOG_LEVEL_WARN) {
      Serial.println("[API] ACK skipped, missing command_id");
    }

    return false;
  }

  if (WiFi.status() != WL_CONNECTED) {
    if (LOG_LEVEL >= LOG_LEVEL_WARN) {
      Serial.println("[API] ACK failed, WiFi not connected");
    }

    return false;
  }

  char path[220];
  char body[260];
  char payload[1024];

  buildAckPath(path, sizeof(path));

  snprintf(
    body,
    sizeof(body),
    "{\"device_id\":\"%s\",\"command_id\":\"%s\",\"status\":\"%s\"}",
    apiDeviceId,
    commandId,
    status
  );

  if (LOG_LEVEL >= LOG_LEVEL_INFO) {
    Serial.print("[API] ACK command_id=");
    Serial.println(commandId);
  }

  if (LOG_LEVEL >= LOG_LEVEL_DEBUG) {
    Serial.print("[API] ACK path ");
    Serial.println(path);
    Serial.print("[API] ACK body ");
    Serial.println(body);
  }

  int httpCode = 0;
  bool requestOk = false;

  for (uint8_t attempt = 0; attempt <= HTTP_RETRY_COUNT; attempt++) {
    payload[0] = '\0';
    httpCode = 0;

    requestOk = rawHttpRequest("POST", path, body, httpCode, payload, sizeof(payload));

    if (requestOk && httpCode >= 200 && httpCode < 500) {
      break;
    }

    if (attempt < HTTP_RETRY_COUNT) {
      if (LOG_LEVEL >= LOG_LEVEL_WARN) {
        Serial.print("[API] ACK transient error, retry ");
        Serial.print(attempt + 1);
        Serial.print("/");
        Serial.println(HTTP_RETRY_COUNT);
      }

      delay(HTTP_RETRY_DELAY_MS);
    }
  }

  if (LOG_LEVEL >= LOG_LEVEL_INFO) {
    Serial.print("[API] ACK HTTP ");
    Serial.println(httpCode);
  }

  if (payload[0] != '\0' && LOG_LEVEL >= LOG_LEVEL_DEBUG) {
    Serial.print("[API] ACK payload: ");
    Serial.println(payload);
  }

  return httpCode >= 200 && httpCode < 300;
}
'@

Write-Utf8NoBom -Path $configPath -Content $configContent
Write-Utf8NoBom -Path $apiPath -Content $apiContent

Write-Host ""
Write-Host "Done."
Write-Host "Check:"
Write-Host "git diff -- firmware/esp32_gate/include/config.h firmware/esp32_gate/src/api_client.cpp"