#pragma once

#include <Arduino.h>

// ===== Piny =====
#define DEBUG_LED_PIN 2          // D2 na płytce, HIGH = świeci
#define BOOT_BUTTON_PIN 0        // przycisk BOOT, aktywny po zwarciu do GND

// Dwa wyjścia do dwóch przycisków pilota
#define GATE1_OUTPUT_PIN 26      // przycisk pilota nr 1 / szlaban 1
#define GATE2_OUTPUT_PIN 27      // przycisk pilota nr 2 / szlaban 2

// ===== Brama / pilot =====
#define GATE_ACTIVE_HIGH 1
#define DEFAULT_GATE_PULSE_MS 700
#define MIN_GATE_PULSE_MS 100
#define MAX_GATE_PULSE_MS 5000

// ===== WiFi =====
#define WIFI_CONNECT_TIMEOUT_MS 20000

// Co ile najczęściej wolno wymuszać reconnect WiFi podczas pracy.
// Chroni przed nerwową pętlą reconnectów.
#define WIFI_RECONNECT_MIN_INTERVAL_MS 15000

// RSSI poniżej tej wartości tylko ostrzegamy w logu.
// Nie reconnectujemy od razu, bo słaby sygnał często nadal działa.
#define WIFI_RSSI_WARN_DBM -75

// ===== API =====
#define POLL_INTERVAL_MS 5000
#define API_TIMEOUT_MS 5000

// Ile kolejnych błędów API powoduje reconnect WiFi/API.
#define API_FAILS_BEFORE_WIFI_RECONNECT 5

// Ile kolejnych błędów API powoduje restart ESP32.
// 60 * 5 s = ok. 5 minut awarii, więc to już nie chwilowa czkawka.
#define API_FAILS_BEFORE_REBOOT 60

// Co ile wypisać status WiFi/API przy normalnej pracy.
#define STATUS_PRINT_INTERVAL_MS 60000

// ===== Oszczędzanie energii =====
// Modem sleep WiFi. Dobre dla pracy z cyklicznym pollingiem.
#define WIFI_POWER_SAVE_ENABLED 1

// Obniżenie taktowania CPU. Do HTTP/pollingu/przekaźnika 80 MHz wystarcza.
#define CPU_POWER_SAVE_ENABLED 1
#define CPU_FREQUENCY_MHZ 80

// ===== Portal konfiguracyjny =====
#define CONFIG_NAMESPACE "gatecfg"
#define CONFIG_AP_PREFIX "GateConfig-"
#define CONFIG_AP_PASSWORD "12345678"
#define CONFIG_PORTAL_DNS_PORT 53

// ===== Logowanie =====
// 0 - cisza
// 1 - tylko błędy
// 2 - ostrzeżenia i błędy
// 3 - normalne informacje: start, WiFi, komendy, ACK
// 4 - debug: każdy polling, payload, command:none
#define LOG_LEVEL_NONE 0
#define LOG_LEVEL_ERROR 1
#define LOG_LEVEL_WARN 2
#define LOG_LEVEL_INFO 3
#define LOG_LEVEL_DEBUG 4

#define LOG_LEVEL LOG_LEVEL_INFO