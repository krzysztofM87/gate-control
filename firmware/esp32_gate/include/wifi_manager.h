#pragma once

#include <Arduino.h>

struct DeviceConfig {
  String wifiSsid;
  String wifiPassword;
  String serverUrl;
  String deviceId;
  String deviceSecret;

  bool isComplete() const {
    return wifiSsid.length() > 0 &&
           serverUrl.length() > 0 &&
           deviceId.length() > 0 &&
           deviceSecret.length() > 0;
  }
};

String getChipId();
String getConfigApSsid();

bool loadDeviceConfig(DeviceConfig &config);
bool saveDeviceConfig(const DeviceConfig &config);
void clearDeviceConfig();

bool connectToConfiguredWiFi(const DeviceConfig &config);
