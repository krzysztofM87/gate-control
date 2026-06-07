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
    Serial.println("[API] Only http:// is supported in stable mode");
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
    Serial.println("[API] Invalid host length");
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

  if (lineSize == 0) {
    return false;
  }

  while (millis() - start < timeoutMs) {
    while (client.available()) {
      char c = (char)client.read();

      if (c == '\r') {
        continue;
      }

      if (c == '\n') {
        line[pos] = '\0';
        return true;
      }

      if (pos < lineSize - 1) {
        line[pos++] = c;
      }
    }

    if (!client.connected() && !client.available()) {
      break;
    }

    delay(1);
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

  Serial.print("[HTTP] Connect ");
  Serial.print(apiHost);
  Serial.print(":");
  Serial.println(apiPort);

  if (!client.connect(apiHost, apiPort)) {
    Serial.println("[HTTP] Connect failed");
    httpCode = -11;
    client.stop();
    return false;
  }

  client.print(method);
  client.print(" ");
  client.print(path);
  client.println(" HTTP/1.0");

  client.print("Host: ");
  client.println(apiHost);

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
    Serial.println("[HTTP] No status line");
    httpCode = -12;
    client.stop();
    return false;
  }

  Serial.print("[HTTP] Status line: ");
  Serial.println(line);

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

    delay(1);
  }

  if (payloadSize > 0) {
    payload[pos] = '\0';
  }

  client.stop();
  delay(50);

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
    Serial.println("[API] Command 'open' without target, defaulting to gate 1");
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

  Serial.print("[API] Server URL: ");
  Serial.println(serverUrl);

  if (!parseServerUrl(serverUrl.c_str())) {
    Serial.println("[API] Server URL parse failed");
  }

  Serial.print("[API] Host: ");
  Serial.println(apiHost);

  Serial.print("[API] Port: ");
  Serial.println(apiPort);

  Serial.print("[API] Base path: ");
  Serial.println(apiBasePath);
}

GateCommand pollGateCommand() {
  GateCommand result;

  if (WiFi.status() != WL_CONNECTED) {
    result.httpCode = -1;
    Serial.println("[API] WiFi not connected");
    return result;
  }

  char path[240];
  char payload[1024];

  buildPollPath(path, sizeof(path));

  Serial.print("[API] Poll ");
  Serial.println(path);

  int httpCode = 0;

  rawHttpRequest("GET", path, "", httpCode, payload, sizeof(payload));

  result.httpCode = httpCode;

  Serial.print("[API] HTTP ");
  Serial.println(httpCode);

  if (payload[0] != '\0') {
    Serial.print("[API] Payload: ");
    Serial.println(payload);
  }

  if (httpCode < 200 || httpCode >= 300) {
    Serial.println("[API] Non-2xx response, ignored");
    return result;
  }

  char command[32];

  if (!extractJsonString(payload, "command", command, sizeof(command))) {
    Serial.println("[API] Missing command field");
    return result;
  }

  Serial.print("[API] Parsed command: ");
  Serial.println(command);

  if (strcmp(command, "none") == 0) {
    Serial.println("[API] No command");
    return result;
  }

  uint8_t target = targetFromCommand(command);

  if (target == GATE_TARGET_NONE) {
    Serial.println("[API] Unknown command");
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

  Serial.print("[API] Command target: ");
  Serial.println(result.target);

  Serial.print("[API] Command ID: ");
  Serial.println(result.commandId);

  Serial.print("[API] Relay ms: ");
  Serial.println(result.relayTimeMs);

  return result;
}

bool ackGateCommand(const char *commandId, const char *status) {
  if (commandId == nullptr || commandId[0] == '\0') {
    Serial.println("[API] ACK skipped, missing command_id");
    return false;
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[API] ACK failed, WiFi not connected");
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

  Serial.print("[API] ACK ");
  Serial.println(path);
  Serial.print("[API] ACK body ");
  Serial.println(body);

  int httpCode = 0;

  rawHttpRequest("POST", path, body, httpCode, payload, sizeof(payload));

  Serial.print("[API] ACK HTTP ");
  Serial.println(httpCode);

  if (payload[0] != '\0') {
    Serial.print("[API] ACK payload: ");
    Serial.println(payload);
  }

  return httpCode >= 200 && httpCode < 300;
}
