#include <Arduino.h>
#include <WiFi.h>
#include <Preferences.h>
#include <esp_wifi.h>

#include "../include/config.h"
#include "../include/wifi_manager.h"
#include "../include/debug_led.h"

String getChipId() {
  uint64_t chipid = ESP.getEfuseMac();
  char id[13];
  snprintf(id, sizeof(id), "%04X%08X", (uint16_t)(chipid >> 32), (uint32_t)chipid);
  return String(id);
}

String getConfigApSsid() {
  return String(CONFIG_AP_PREFIX) + getChipId().substring(8);
}

static String normalizeServerUrl(String url) {
  url.trim();

  while (url.endsWith("/")) {
    url.remove(url.length() - 1);
  }

  return url;
}

static void setupWifiPowerSave() {
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);

#if WIFI_POWER_SAVE_ENABLED
  WiFi.setSleep(true);
  esp_wifi_set_ps(WIFI_PS_MIN_MODEM);

  if (LOG_LEVEL >= LOG_LEVEL_INFO) {
    Serial.println("[WIFI] Power save: modem sleep enabled");
  }
#else
  WiFi.setSleep(false);
  esp_wifi_set_ps(WIFI_PS_NONE);

  if (LOG_LEVEL >= LOG_LEVEL_INFO) {
    Serial.println("[WIFI] Power save: disabled");
  }
#endif
}

bool loadDeviceConfig(DeviceConfig &config) {
  Preferences prefs;

  if (!prefs.begin(CONFIG_NAMESPACE, true)) {
    if (LOG_LEVEL >= LOG_LEVEL_ERROR) {
      Serial.println("[CFG] Preferences open failed");
    }
    return false;
  }

  config.wifiSsid = prefs.getString("wifi_ssid", "");
  config.wifiPassword = prefs.getString("wifi_pass", "");
  config.serverUrl = prefs.getString("server_url", "");
  config.deviceId = prefs.getString("device_id", "");
  config.deviceSecret = prefs.getString("dev_secret", "");

  prefs.end();

  config.serverUrl = normalizeServerUrl(config.serverUrl);

  if (LOG_LEVEL >= LOG_LEVEL_INFO) {
    Serial.println("[CFG] Loaded");
    Serial.print("[CFG] wifiSsid=");
    Serial.println(config.wifiSsid);
    Serial.print("[CFG] serverUrl=");
    Serial.println(config.serverUrl);
    Serial.print("[CFG] deviceId=");
    Serial.println(config.deviceId);
  }

  return config.isComplete();
}

bool saveDeviceConfig(const DeviceConfig &config) {
  Preferences prefs;

  if (!prefs.begin(CONFIG_NAMESPACE, false)) {
    if (LOG_LEVEL >= LOG_LEVEL_ERROR) {
      Serial.println("[CFG] Preferences open failed for write");
    }
    return false;
  }

  prefs.putString("wifi_ssid", config.wifiSsid);
  prefs.putString("wifi_pass", config.wifiPassword);
  prefs.putString("server_url", normalizeServerUrl(config.serverUrl));
  prefs.putString("device_id", config.deviceId);
  prefs.putString("dev_secret", config.deviceSecret);

  prefs.end();

  if (LOG_LEVEL >= LOG_LEVEL_INFO) {
    Serial.println("[CFG] Saved");
  }

  return true;
}

void clearDeviceConfig() {
  Preferences prefs;

  if (prefs.begin(CONFIG_NAMESPACE, false)) {
    prefs.clear();
    prefs.end();
  }

  if (LOG_LEVEL >= LOG_LEVEL_INFO) {
    Serial.println("[CFG] Cleared");
  }
}

bool connectToConfiguredWiFi(const DeviceConfig &config) {
  if (config.wifiSsid.length() == 0) {
    if (LOG_LEVEL >= LOG_LEVEL_ERROR) {
      Serial.println("[WIFI] Missing SSID");
    }
    return false;
  }

  WiFi.mode(WIFI_STA);
  setupWifiPowerSave();

  // Nie używamy disconnect(true), bo to brutalnie czyści/rozłącza radio.
  // Przy reconnect wystarczy zwykłe rozłączenie bez kasowania konfiguracji.
  if (WiFi.status() == WL_CONNECTED) {
    WiFi.disconnect(false, false);
    delay(250);
  }

  if (LOG_LEVEL >= LOG_LEVEL_INFO) {
    Serial.print("[WIFI] Connecting to ");
    Serial.println(config.wifiSsid);
  }

  WiFi.begin(config.wifiSsid.c_str(), config.wifiPassword.c_str());

  unsigned long start = millis();

  while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_CONNECT_TIMEOUT_MS) {
    blinkDebugLedOnce();
    delay(400);

    if (LOG_LEVEL >= LOG_LEVEL_DEBUG) {
      Serial.print(".");
    }
  }

  if (LOG_LEVEL >= LOG_LEVEL_DEBUG) {
    Serial.println();
  }

  if (WiFi.status() == WL_CONNECTED) {
    if (LOG_LEVEL >= LOG_LEVEL_INFO) {
      Serial.println("[WIFI] Connected");
      Serial.print("[WIFI] IP: ");
      Serial.println(WiFi.localIP());
      Serial.print("[WIFI] RSSI: ");
      Serial.println(WiFi.RSSI());
    }

    blinkDebugLed(3, 100, 100);
    return true;
  }

  if (LOG_LEVEL >= LOG_LEVEL_WARN) {
    Serial.println("[WIFI] Connection failed");
  }

  return false;
}