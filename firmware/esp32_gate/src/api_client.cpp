#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

#include "../include/config.h"
#include "../include/api_client.h"
#include "../include/gate_control.h"

static DeviceConfig apiConfig;

static String normalizeBaseUrl(String url) {
  url.trim();

  while (url.endsWith("/")) {
    url.remove(url.length() - 1);
  }

  return url;
}

static String urlEncode(const String &value) {
  String encoded = "";

  for (size_t i = 0; i < value.length(); i++) {
    char c = value.charAt(i);

    if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
      encoded += c;
    } else if (c == ' ') {
      encoded += "%20";
    } else {
      char buf[4];
      snprintf(buf, sizeof(buf), "%%%02X", (uint8_t)c);
      encoded += buf;
    }
  }

  return encoded;
}

static String jsonEscape(const String &value) {
  String out = value;
  out.replace("\\", "\\\\");
  out.replace("\"", "\\\"");
  out.replace("\n", "\\n");
  out.replace("\r", "\\r");
  return out;
}

static String extractJsonString(const String &json, const String &key) {
  String pattern = "\"" + key + "\"";
  int keyPos = json.indexOf(pattern);

  if (keyPos < 0) {
    return "";
  }

  int colonPos = json.indexOf(":", keyPos + pattern.length());

  if (colonPos < 0) {
    return "";
  }

  int firstQuote = json.indexOf("\"", colonPos + 1);

  if (firstQuote < 0) {
    return "";
  }

  int secondQuote = json.indexOf("\"", firstQuote + 1);

  if (secondQuote < 0) {
    return "";
  }

  return json.substring(firstQuote + 1, secondQuote);
}

static int extractJsonInt(const String &json, const String &key, int defaultValue) {
  String pattern = "\"" + key + "\"";
  int keyPos = json.indexOf(pattern);

  if (keyPos < 0) {
    return defaultValue;
  }

  int colonPos = json.indexOf(":", keyPos + pattern.length());

  if (colonPos < 0) {
    return defaultValue;
  }

  int start = colonPos + 1;

  while (start < (int)json.length() && isspace(json.charAt(start))) {
    start++;
  }

  int end = start;

  while (end < (int)json.length() && isdigit(json.charAt(end))) {
    end++;
  }

  if (end == start) {
    return defaultValue;
  }

  return json.substring(start, end).toInt();
}

static uint8_t parseGateTargetFromPayload(const String &payload, const String &command) {
  if (command == "open_1") {
    return GATE_TARGET_1;
  }

  if (command == "open_2") {
    return GATE_TARGET_2;
  }

  if (command == "open_both") {
    return GATE_TARGET_BOTH;
  }

  String gateText = extractJsonString(payload, "gate");

  if (gateText == "1" || gateText == "gate1" || gateText == "left") {
    return GATE_TARGET_1;
  }

  if (gateText == "2" || gateText == "gate2" || gateText == "right") {
    return GATE_TARGET_2;
  }

  if (gateText == "both") {
    return GATE_TARGET_BOTH;
  }

  int gateNumber = extractJsonInt(payload, "gate", 0);

  if (gateNumber == 1) {
    return GATE_TARGET_1;
  }

  if (gateNumber == 2) {
    return GATE_TARGET_2;
  }

  if (gateNumber == 3) {
    return GATE_TARGET_BOTH;
  }

  int buttonNumber = extractJsonInt(payload, "button", 0);

  if (buttonNumber == 1) {
    return GATE_TARGET_1;
  }

  if (buttonNumber == 2) {
    return GATE_TARGET_2;
  }

  if (buttonNumber == 3) {
    return GATE_TARGET_BOTH;
  }

  int channelNumber = extractJsonInt(payload, "channel", 0);

  if (channelNumber == 1) {
    return GATE_TARGET_1;
  }

  if (channelNumber == 2) {
    return GATE_TARGET_2;
  }

  if (channelNumber == 3) {
    return GATE_TARGET_BOTH;
  }

  if (command == "open") {
    Serial.println("[API] Command 'open' without gate target, defaulting to gate 1");
    return GATE_TARGET_1;
  }

  return GATE_TARGET_NONE;
}

static void addCommonHeaders(HTTPClient &http) {
  http.addHeader("X-Device-Id", apiConfig.deviceId);
  http.addHeader("X-Device-Secret", apiConfig.deviceSecret);
  http.addHeader("Cache-Control", "no-cache");
}

void setupApiClient(const DeviceConfig &config) {
  apiConfig = config;
  apiConfig.serverUrl = normalizeBaseUrl(apiConfig.serverUrl);

  Serial.print("[API] Server URL: ");
  Serial.println(apiConfig.serverUrl);
}

GateCommand pollGateCommand() {
  GateCommand result;

  if (WiFi.status() != WL_CONNECTED) {
    result.httpCode = -1;
    Serial.println("[API] WiFi not connected");
    return result;
  }

  String url = apiConfig.serverUrl + "/api/device/poll?device_id=" + urlEncode(apiConfig.deviceId);

  HTTPClient http;
  http.setTimeout(API_TIMEOUT_MS);

  int httpCode = 0;
  String payload = "";

  Serial.print("[API] Poll ");
  Serial.println(url);

  if (url.startsWith("https://")) {
    WiFiClientSecure client;
    client.setInsecure(); // MVP. Produkcyjnie lepiej dodać CA/pinning certyfikatu.
    http.begin(client, url);
    addCommonHeaders(http);
    httpCode = http.GET();
    payload = http.getString();
    http.end();
  } else {
    WiFiClient client;
    http.begin(client, url);
    addCommonHeaders(http);
    httpCode = http.GET();
    payload = http.getString();
    http.end();
  }

  result.httpCode = httpCode;
  result.raw = payload;

  Serial.print("[API] HTTP ");
  Serial.println(httpCode);

  if (payload.length() > 0) {
    Serial.print("[API] Payload: ");
    Serial.println(payload);
  }

  if (httpCode == 204) {
    return result;
  }

  if (httpCode < 200 || httpCode >= 300) {
    return result;
  }

  String command = extractJsonString(payload, "command");

  if (command == "open" || command == "open_1" || command == "open_2" || command == "open_both") {
    uint8_t target = parseGateTargetFromPayload(payload, command);

    if (target != GATE_TARGET_NONE) {
      result.shouldOpen = true;
      result.target = target;
      result.commandId = extractJsonString(payload, "command_id");

      int relayMs = extractJsonInt(payload, "relay_time_ms", DEFAULT_GATE_PULSE_MS);

      if (relayMs < MIN_GATE_PULSE_MS) {
        relayMs = MIN_GATE_PULSE_MS;
      }

      if (relayMs > MAX_GATE_PULSE_MS) {
        relayMs = MAX_GATE_PULSE_MS;
      }

      result.relayTimeMs = relayMs;
    }
  }

  return result;
}

bool ackGateCommand(const String &commandId, const String &status) {
  if (commandId.length() == 0) {
    Serial.println("[API] ACK skipped, missing command_id");
    return false;
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[API] ACK failed, WiFi not connected");
    return false;
  }

  String url = apiConfig.serverUrl + "/api/device/ack";

  String body = "{";
  body += "\"device_id\":\"" + jsonEscape(apiConfig.deviceId) + "\",";
  body += "\"command_id\":\"" + jsonEscape(commandId) + "\",";
  body += "\"status\":\"" + jsonEscape(status) + "\"";
  body += "}";

  HTTPClient http;
  http.setTimeout(API_TIMEOUT_MS);

  int httpCode = 0;
  String payload = "";

  Serial.print("[API] ACK ");
  Serial.println(url);
  Serial.print("[API] ACK body ");
  Serial.println(body);

  if (url.startsWith("https://")) {
    WiFiClientSecure client;
    client.setInsecure(); // MVP. Produkcyjnie lepiej dodać CA/pinning certyfikatu.
    http.begin(client, url);
    addCommonHeaders(http);
    http.addHeader("Content-Type", "application/json");
    httpCode = http.POST(body);
    payload = http.getString();
    http.end();
  } else {
    WiFiClient client;
    http.begin(client, url);
    addCommonHeaders(http);
    http.addHeader("Content-Type", "application/json");
    httpCode = http.POST(body);
    payload = http.getString();
    http.end();
  }

  Serial.print("[API] ACK HTTP ");
  Serial.println(httpCode);

  if (payload.length() > 0) {
    Serial.print("[API] ACK payload: ");
    Serial.println(payload);
  }

  return httpCode >= 200 && httpCode < 300;
}
