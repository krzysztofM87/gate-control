#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>

#include "../include/config.h"
#include "../include/config_portal.h"
#include "../include/wifi_manager.h"
#include "../include/debug_led.h"

static WebServer server(80);
static DNSServer dnsServer;
static DeviceConfig portalConfig;

static String htmlEscape(String value) {
  value.replace("&", "&amp;");
  value.replace("<", "&lt;");
  value.replace(">", "&gt;");
  value.replace("\"", "&quot;");
  return value;
}

static String pageHeader(const String &title) {
  String html;
  html += "<!doctype html><html lang='pl'><head>";
  html += "<meta charset='utf-8'>";
  html += "<meta name='viewport' content='width=device-width, initial-scale=1'>";
  html += "<title>" + title + "</title>";
  html += "<style>";
  html += "body{font-family:Arial,sans-serif;margin:0;background:#f5f5f5;color:#222}";
  html += ".wrap{max-width:520px;margin:30px auto;background:white;padding:22px;border-radius:12px;box-shadow:0 2px 16px #0002}";
  html += "h1{font-size:22px;margin:0 0 15px}";
  html += "label{display:block;margin-top:12px;font-weight:bold}";
  html += "input{width:100%;box-sizing:border-box;padding:11px;margin-top:5px;border:1px solid #ccc;border-radius:8px;font-size:16px}";
  html += "button{margin-top:18px;width:100%;padding:13px;border:0;border-radius:8px;background:#111;color:white;font-size:17px}";
  html += ".muted{color:#666;font-size:13px;line-height:1.4}";
  html += ".danger{background:#8b0000}";
  html += "</style></head><body><div class='wrap'>";
  return html;
}

static String pageFooter() {
  return "</div></body></html>";
}

static void handleRoot() {
  String defaultDeviceId = portalConfig.deviceId;
  if (defaultDeviceId.length() == 0) {
    defaultDeviceId = "esp32-" + getChipId();
  }

  String html = pageHeader("Konfiguracja bramy");

  html += "<h1>Konfiguracja sterownika bramy</h1>";
  html += "<p class='muted'>Po zapisaniu ESP32 uruchomi się ponownie i spróbuje połączyć z WiFi oraz serwerem.</p>";

  html += "<form method='POST' action='/save'>";

  html += "<label>SSID WiFi</label>";
  html += "<input name='wifi_ssid' required value='" + htmlEscape(portalConfig.wifiSsid) + "'>";

  html += "<label>Hasło WiFi</label>";
  html += "<input name='wifi_password' type='password' placeholder='zostaw puste, aby nie zmieniać'>";

  html += "<label>Adres serwera</label>";
  html += "<input name='server_url' required value='" + htmlEscape(portalConfig.serverUrl) + "' placeholder='https://tools.malmaz.com/gate'>";

  html += "<label>ID urządzenia</label>";
  html += "<input name='device_id' required value='" + htmlEscape(defaultDeviceId) + "'>";

  html += "<label>Sekret urządzenia</label>";
  html += "<input name='device_secret' type='password' required placeholder='wpisz sekret urządzenia'>";

  html += "<button type='submit'>Zapisz konfigurację</button>";
  html += "</form>";

  html += "<form method='POST' action='/clear'>";
  html += "<button class='danger' type='submit'>Wyczyść konfigurację</button>";
  html += "</form>";

  html += "<p class='muted'>AP: " + htmlEscape(getConfigApSsid()) + "<br>IP: 192.168.4.1</p>";

  html += pageFooter();

  server.send(200, "text/html; charset=utf-8", html);
}

static void handleSave() {
  DeviceConfig newConfig;

  newConfig.wifiSsid = server.arg("wifi_ssid");
  newConfig.wifiPassword = server.arg("wifi_password");
  newConfig.serverUrl = server.arg("server_url");
  newConfig.deviceId = server.arg("device_id");
  newConfig.deviceSecret = server.arg("device_secret");

  newConfig.wifiSsid.trim();
  newConfig.wifiPassword.trim();
  newConfig.serverUrl.trim();
  newConfig.deviceId.trim();
  newConfig.deviceSecret.trim();

  if (newConfig.wifiPassword.length() == 0) {
    newConfig.wifiPassword = portalConfig.wifiPassword;
  }

  if (newConfig.deviceSecret.length() == 0) {
    newConfig.deviceSecret = portalConfig.deviceSecret;
  }

  if (!newConfig.isComplete()) {
    server.send(400, "text/plain; charset=utf-8", "Brakuje wymaganych pól.");
    return;
  }

  saveDeviceConfig(newConfig);

  String html = pageHeader("Zapisano");
  html += "<h1>Zapisano konfigurację</h1>";
  html += "<p>ESP32 uruchomi się ponownie za chwilę.</p>";
  html += pageFooter();

  server.send(200, "text/html; charset=utf-8", html);

  delay(1000);
  ESP.restart();
}

static void handleClear() {
  clearDeviceConfig();

  String html = pageHeader("Wyczyszczono");
  html += "<h1>Wyczyszczono konfigurację</h1>";
  html += "<p>ESP32 uruchomi się ponownie.</p>";
  html += pageFooter();

  server.send(200, "text/html; charset=utf-8", html);

  delay(1000);
  ESP.restart();
}

static void handleNotFound() {
  server.sendHeader("Location", "/", true);
  server.send(302, "text/plain", "");
}

void runConfigPortal(DeviceConfig currentConfig) {
  portalConfig = currentConfig;

  Serial.println("[PORTAL] Starting config portal");

  WiFi.disconnect(true);
  delay(300);

  WiFi.mode(WIFI_AP);

  String apSsid = getConfigApSsid();

  bool apStarted = WiFi.softAP(apSsid.c_str(), CONFIG_AP_PASSWORD);

  if (!apStarted) {
    Serial.println("[PORTAL] AP start failed");
    return;
  }

  IPAddress apIp = WiFi.softAPIP();

  Serial.print("[PORTAL] SSID: ");
  Serial.println(apSsid);
  Serial.print("[PORTAL] Password: ");
  Serial.println(CONFIG_AP_PASSWORD);
  Serial.print("[PORTAL] IP: ");
  Serial.println(apIp);

  dnsServer.start(CONFIG_PORTAL_DNS_PORT, "*", apIp);

  server.on("/", HTTP_GET, handleRoot);
  server.on("/save", HTTP_POST, handleSave);
  server.on("/clear", HTTP_POST, handleClear);
  server.onNotFound(handleNotFound);
  server.begin();

  Serial.println("[PORTAL] HTTP server started");

  unsigned long lastBlink = 0;
  bool ledState = false;

  while (true) {
    dnsServer.processNextRequest();
    server.handleClient();

    if (millis() - lastBlink > 600) {
      lastBlink = millis();
      ledState = !ledState;

      if (ledState) {
        debugLedOn();
      } else {
        debugLedOff();
      }
    }

    delay(5);
  }
}
