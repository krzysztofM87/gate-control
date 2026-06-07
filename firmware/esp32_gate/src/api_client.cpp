#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>

#include "../include/config.h"
#include "../include/api_client.h"
#include "../include/gate_control.h"

static DeviceConfig apiConfig;

struct ParsedUrl {
  bool ok = false;
  String host = "";
  uint16_t port = 80;
  String path = "/";
};

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

    if ((c >= 'a' && c <= 'z') ||
        (c >= 'A' && c <= 'Z') ||
        (c >= '0' && c <= '9') ||
        c == '-' || c == '_' || c == '.' || c == '~') {
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

static bool parseHttpUrl(const String &url, ParsedUrl &parsed) {
  if (!url.startsWith("http://")) {
    Serial.println("[HTTP] Only http:// is supported in stable raw client mode");
    return false;
  }

  String rest = url.substring(7);

  int slashPos = rest.indexOf("/");
  String hostPort;

  if (slashPos >= 0) {
    hostPort = rest.substring(0, slashPos);
    parsed.path = rest.substring(slashPos);
  } else {
    hostPort = rest;
    parsed.path = "/";
  }

  int colonPos = hostPort.indexOf(":");

  if (colonPos >= 0) {
    parsed.host = hostPort.substring(0, colonPos);
    parsed.port = (uint16_t)hostPort.substring(colonPos + 1).toInt();

    if (parsed.port == 0) {
      parsed.port = 80;
    }
  } else {
    parsed.host = hostPort;
    parsed.port = 80;
  }

  parsed.host.trim();

  if (parsed.host.length() == 0) {
    return false;
  }

  parsed.ok = true;
  return true;
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

  while (start < (int)json.length()) {
    char c = json.charAt(start);
    if (c != ' ' && c != '\t' && c != '\r' && c != '\n') {
      break;
    }
    start++;
  }

  int end = start;

  while (end < (int)json.length()) {
    char c = json.charAt(end);
    if (c < '0' || c > '9') {
      break;
    }
    end++;
  }

  if (end == start) {
    return defaultValue;
  }

  return json.substring(start, end).toInt();
}

static bool payloadHasCommandNone(const String &payload) {
  return payload.indexOf("\"command\":\"none\"") >= 0 ||
         payload.indexOf("\"command\": \"none\"") >= 0;
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

  if (command == "open") {
    Serial.println("[API] Command 'open' without gate target, defaulting to gate 1");
    return GATE_TARGET_1;
  }

  return GATE_TARGET_NONE;
}

static bool rawHttpRequest(
  const String &method,
  const String &url,
  const String &body,
  int &httpCode,
  String &payload
) {
  httpCode = 0;
  payload = "";

  ParsedUrl parsed;

  if (!parseHttpUrl(url, parsed)) {
    httpCode = -10;
    return false;
  }

  WiFiClient client;
  client.setTimeout(API_TIMEOUT_MS);

  Serial.print("[HTTP] Connect ");
  Serial.print(parsed.host);
  Serial.print(":");
  Serial.println(parsed.port);

  if (!client.connect(parsed.host.c_str(), parsed.port)) {
    Serial.println("[HTTP] Connect failed");
    httpCode = -11;
    client.stop();
    return false;
  }

  client.print(method);
  client.print(" ");
  client.print(parsed.path);
  client.println(" HTTP/1.0");

  client.print("Host: ");
  client.println(parsed.host);

  client.print("X-Device-Id: ");
  client.println(apiConfig.deviceId);

  client.print("X-Device-Secret: ");
  client.println(apiConfig.deviceSecret);

  client.println("Cache-Control: no-cache");
  client.println("Connection: close");

  if (method == "POST") {
    client.println("Content-Type: application/json");

    client.print("Content-Length: ");
    client.println(body.length());
  }

  client.println();

  if (method == "POST") {
    client.print(body);
  }

  unsigned long start = millis();
  String response = "";

  while (client.connected() || client.available()) {
    while (client.available()) {
      char c = (char)client.read();
      response += c;

      // Zabezpieczenie przed przypadkowym zalaniem RAM.
      if (response.length() > 4096) {
        Serial.println("[HTTP] Response too large, stopping read");
        client.stop();
        break;
      }
    }

    if (millis() - start > API_TIMEOUT_MS) {
      Serial.println("[HTTP] Read timeout");
      client.stop();
      break;
    }

    delay(1);
  }

  client.stop();
  delay(20);

  if (response.length() == 0) {
    Serial.println("[HTTP] Empty response");
    httpCode = -12;
    return false;
  }

  int firstLineEnd = response.indexOf("\r\n");

  if (firstLineEnd < 0) {
    Serial.println("[HTTP] Invalid response, no status line");
    httpCode = -13;
    return false;
  }

  String statusLine = response.substring(0, firstLineEnd);
  Serial.print("[HTTP] Status line: ");
  Serial.println(statusLine);

  int firstSpace = statusLine.indexOf(" ");
  int secondSpace = statusLine.indexOf(" ", firstSpace + 1);

  if (firstSpace >= 0 && secondSpace > firstSpace) {
    httpCode = statusLine.substring(firstSpace + 1, secondSpace).toInt();
  } else {
    httpCode = -14;
  }

  int headerEnd = response.indexOf("\r\n\r\n");

  if (headerEnd >= 0) {
    payload = response.substring(headerEnd + 4);
  } else {
    payload = "";
  }

  payload.trim();

  return httpCode >= 100;
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

  Serial.print("[API] Poll ");
  Serial.println(url);

  int httpCode = 0;
  String payload = "";

  rawHttpRequest("GET", url, "", httpCode, payload);

  result.httpCode = httpCode;

  Serial.print("[API] HTTP ");
  Serial.println(httpCode);

  if (payload.length() > 0) {
    Serial.print("[API] Payload: ");
    Serial.println(payload);
  }

  if (httpCode == 204) {
    Serial.println("[API] No content");
    return result;
  }

  if (httpCode < 200 || httpCode >= 300) {
    Serial.println("[API] Non-2xx response, ignored");
    return result;
  }

  if (payloadHasCommandNone(payload)) {
    Serial.println("[API] No command");
    return result;
  }

  String command = extractJsonString(payload, "command");

  Serial.print("[API] Parsed command: ");
  Serial.println(command);

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

      Serial.print("[API] Command target: ");
      Serial.println(result.target);

      Serial.print("[API] Command ID: ");
      Serial.println(result.commandId);

      Serial.print("[API] Relay ms: ");
      Serial.println(result.relayTimeMs);
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

  Serial.print("[API] ACK ");
  Serial.println(url);
  Serial.print("[API] ACK body ");
  Serial.println(body);

  int httpCode = 0;
  String payload = "";

  rawHttpRequest("POST", url, body, httpCode, payload);

  Serial.print("[API] ACK HTTP ");
  Serial.println(httpCode);

  if (payload.length() > 0) {
    Serial.print("[API] ACK payload: ");
    Serial.println(payload);
  }

  return httpCode >= 200 && httpCode < 300;
}
