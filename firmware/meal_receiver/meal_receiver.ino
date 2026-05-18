/*
 * Meal Plan E-Ink Display — Deep Sleep Edition
 * ELECROW CrowPanel ESP32-S3 5.79" E-Paper (GDEY0579T93, dual SSD1683)
 *
 * Power management:
 *   - Deep sleeps most of the time (~10µA)
 *   - Wakes daily at 02:00 local to fetch & display current week's meal plan
 *   - Wakes on button press; stays awake 2.5s after last button activity
 *   - Syncs NTP on every wake to keep RTC accurate
 *   - E-ink retains displayed image with zero power during sleep
 *
 * Battery life estimate: 2100mAh → months (vs ~14h always-on)
 *
 * First boot / no WiFi saved:
 *   - Stays in AP mode (no sleep) for WiFi + server configuration
 *   - After saving WiFi, reboots into normal deep-sleep cycle
 *
 * Install in Arduino IDE Library Manager:
 *   - GxEPD2 by Jean-Marc Zingg
 *   - Adafruit GFX Library
 *   - Adafruit BusIO
 *
 * Board settings (Tools menu):
 *   - Board: ESP32S3 Dev Module
 *   - PSRAM: OPI PSRAM
 *   - Flash Size: 8MB
 *   - Partition Scheme: Huge APP (3MB No OTA / 1MB SPIFFS)
 *   - USB CDC On Boot: Enabled
 */

#ifndef BOARD_HAS_PSRAM
#error "Please enable PSRAM! (Arduino IDE: Tools > PSRAM > OPI PSRAM)"
#endif

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <SPI.h>
#include <time.h>
#include <esp_sleep.h>

#define ENABLE_GxEPD2_GFX 0
#include <GxEPD2_BW.h>

// ===== PIN DEFINITIONS =====
#define EINK_SCK    12
#define EINK_MOSI   11
#define EINK_CS     45
#define EINK_DC     46
#define EINK_RST    47
#define EINK_BUSY   48
#define EINK_POWER   7

#define BTN_HOME     2
#define BTN_EXIT     1
#define BTN_ROT_UP   6
#define BTN_ROT_DN   4
#define BTN_ROT_OK   5

// ===== DISPLAY =====
GxEPD2_BW<GxEPD2_579_GDEY0579T93, GxEPD2_579_GDEY0579T93::HEIGHT>
  display(GxEPD2_579_GDEY0579T93(EINK_CS, EINK_DC, EINK_RST, EINK_BUSY));
SPIClass ePaperSPI(FSPI);

#define IMG_WIDTH    792
#define IMG_HEIGHT   272
#define IMG_BUF_SIZE ((IMG_WIDTH + 7) / 8 * IMG_HEIGHT)

// ===== CONFIG =====
const char* AP_SSID = "MealDisplay";
const char* AP_PASS = "12345678";

// NTP — using POSIX TZ for reliable DST handling
const char* NTP_SERVER = "pool.ntp.org";
// EST5EDT = UTC-5, DST starts 2nd Sun Mar, ends 1st Sun Nov
const char* POSIX_TZ = "EST5EDT,M3.2.0,M11.1.0";

// Deep sleep
#define WAKE_HOUR      2   // 02:00 local
#define WAKE_MINUTE    0
#define ACTIVE_TIMEOUT 2500  // ms of button inactivity → sleep
#define DEBOUNCE_MS    300

// ===== GLOBALS =====
WebServer server(80);
Preferences prefs;
String serverIP;
int serverPort = 5000;
int weekOffset = 0;
bool useAPMode = false;
unsigned long lastActivity = 0;
static uint8_t *imgBuffer = NULL;

// ===== DISPLAY HELPERS =====

void enableDisplayPower() {
  pinMode(EINK_POWER, OUTPUT);
  digitalWrite(EINK_POWER, HIGH);
  delay(100);
}

void initDisplay() {
  enableDisplayPower();
  ePaperSPI.begin(EINK_SCK, -1, EINK_MOSI, EINK_CS);
  display.epd2.selectSPI(ePaperSPI, SPISettings(4000000, MSBFIRST, SPI_MODE0));
  display.init(115200, true, 2, false);
  display.setRotation(0);
}

void displayText(const char* line1, const char* line2 = NULL, const char* line3 = NULL) {
  display.setFullWindow();
  display.firstPage();
  do {
    display.fillScreen(GxEPD_WHITE);
    display.setTextColor(GxEPD_BLACK);
    display.setTextSize(3);
    display.setCursor(20, 60);
    display.print(line1);
    if (line2) {
      display.setTextSize(2);
      display.setCursor(20, 120);
      display.print(line2);
    }
    if (line3) {
      display.setTextSize(2);
      display.setCursor(20, 170);
      display.print(line3);
    }
  } while (display.nextPage());
}

void displayRawImage(uint8_t *data, const char* statusLine = NULL) {
  display.setFullWindow();
  display.firstPage();
  do {
    display.drawBitmap(0, 0, data, IMG_WIDTH, IMG_HEIGHT, GxEPD_BLACK);
    // Overlay a small status line at bottom-left
    if (statusLine) {
      // White background strip for readability
      display.fillRect(0, IMG_HEIGHT - 12, 350, 12, GxEPD_WHITE);
      display.setTextColor(GxEPD_BLACK);
      display.setTextSize(1);
      display.setCursor(2, IMG_HEIGHT - 10);
      display.print(statusLine);
    }
  } while (display.nextPage());
}

// ===== WIFI =====

bool connectWiFi() {
  String ssid = prefs.getString("ssid", "");
  String pass = prefs.getString("pass", "");
  if (ssid.length() == 0) return false;

  Serial.printf("Connecting to %s...\n", ssid.c_str());
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid.c_str(), pass.c_str());

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 40) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("Connected! IP: %s\n", WiFi.localIP().toString().c_str());
    return true;
  }
  Serial.println("WiFi connection failed");
  return false;
}

// ===== NTP =====

bool syncTime() {
  // Start NTP first, then apply timezone after time is obtained
  configTime(0, 0, NTP_SERVER);

  struct tm t;
  int retries = 0;
  while (!getLocalTime(&t, 1000) && retries < 15) {
    delay(1000);
    retries++;
  }

  // Now apply timezone — must be after configTime has set the system clock
  setenv("TZ", POSIX_TZ, 1);
  tzset();

  if (getLocalTime(&t)) {
    Serial.printf("Time synced: %04d-%02d-%02d %02d:%02d:%02d\n",
                  t.tm_year + 1900, t.tm_mon + 1, t.tm_mday,
                  t.tm_hour, t.tm_min, t.tm_sec);
    return true;
  }
  Serial.println("NTP sync failed");
  return false;
}

// ===== IMAGE FETCH =====

bool fetchMealImage(int offset) {
  if (serverIP.length() == 0 || WiFi.status() != WL_CONNECTED) return false;

  if (!imgBuffer) {
    imgBuffer = (uint8_t *)ps_malloc(IMG_BUF_SIZE + 1024);
    if (!imgBuffer) return false;
  }

  String url = "http://" + serverIP + ":" + String(serverPort)
             + "/meal_image?offset=" + String(offset);
  Serial.printf("Fetching: %s\n", url.c_str());

  HTTPClient http;
  http.begin(url);
  http.setTimeout(15000);
  int httpCode = http.GET();

  if (httpCode != 200) {
    Serial.printf("HTTP error: %d\n", httpCode);
    http.end();
    return false;
  }

  int contentLen = http.getSize();
  if (contentLen != IMG_BUF_SIZE) {
    Serial.printf("Wrong size: %d (need %d)\n", contentLen, IMG_BUF_SIZE);
    http.end();
    return false;
  }

  WiFiClient *stream = http.getStreamPtr();
  size_t received = 0;
  while (received < IMG_BUF_SIZE && http.connected()) {
    size_t avail = stream->available();
    if (avail) {
      size_t toRead = min(avail, (size_t)(IMG_BUF_SIZE - received));
      stream->readBytes(imgBuffer + received, toRead);
      received += toRead;
    }
    delay(1);
  }
  http.end();

  if (received == IMG_BUF_SIZE) {
    // Build status line showing current time and wake reason
    struct tm t;
    char status[80] = "";
    if (getLocalTime(&t)) {
      esp_sleep_wakeup_cause_t w = esp_sleep_get_wakeup_cause();
      const char* wr = (w == ESP_SLEEP_WAKEUP_TIMER) ? "timer" :
                       (w == ESP_SLEEP_WAKEUP_EXT1) ? "button" : "boot";
      snprintf(status, sizeof(status), "Fetched %02d:%02d [%s] | Next wake ~%02d:%02d",
               t.tm_hour, t.tm_min, wr, WAKE_HOUR, WAKE_MINUTE);
    }
    displayRawImage(imgBuffer, status[0] ? status : NULL);
    Serial.println("Image displayed");
    return true;
  }
  return false;
}

// ===== DEEP SLEEP =====

// Stored for status display
int sleepDurationSecs = 0;

uint64_t microsUntilWake() {
  struct tm t;
  if (!getLocalTime(&t)) {
    Serial.println("WARNING: getLocalTime failed, sleeping 1h");
    sleepDurationSecs = 3600;
    return 3600ULL * 1000000ULL;
  }

  // Calculate seconds until next WAKE_HOUR:WAKE_MINUTE
  int nowSecs = t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec;
  int wakeSecs = WAKE_HOUR * 3600 + WAKE_MINUTE * 60;
  int diff = wakeSecs - nowSecs;
  if (diff <= 60) {
    diff += 86400;
  }

  sleepDurationSecs = diff;
  Serial.printf("Now: %02d:%02d:%02d → Sleep %ds (%.1fh) → Wake %02d:%02d\n",
                t.tm_hour, t.tm_min, t.tm_sec,
                diff, diff / 3600.0, WAKE_HOUR, WAKE_MINUTE);
  return (uint64_t)diff * 1000000ULL;
}

void enterDeepSleep() {
  Serial.println("Preparing for deep sleep...");

  // Disconnect WiFi to save power during shutdown
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  delay(100);

  // Timer wakeup: next WAKE_HOUR:WAKE_MINUTE local
  uint64_t sleepUs = microsUntilWake();
  esp_sleep_enable_timer_wakeup(sleepUs);

  // GPIO wakeup: any button press (active LOW)
  // On ESP32-S3, use gpio wakeup for deep sleep
  uint64_t btnMask = (1ULL << BTN_ROT_UP) | (1ULL << BTN_ROT_DN) | (1ULL << BTN_ROT_OK);
  esp_sleep_enable_ext1_wakeup(btnMask, ESP_EXT1_WAKEUP_ANY_LOW);

  Serial.println("Entering deep sleep...");
  Serial.flush();

  // Power down display controller (e-ink retains image)
  display.powerOff();

  esp_deep_sleep_start();
  // Never reaches here
}

// ===== BUTTONS (active mode) =====

void setupButtons() {
  pinMode(BTN_ROT_UP, INPUT_PULLUP);
  pinMode(BTN_ROT_DN, INPUT_PULLUP);
  pinMode(BTN_ROT_OK, INPUT_PULLUP);
  pinMode(BTN_HOME, INPUT_PULLUP);
  pinMode(BTN_EXIT, INPUT_PULLUP);
}

// Returns true if a button was pressed
bool checkButtons() {
  if (millis() - lastActivity < DEBOUNCE_MS) return false;

  if (digitalRead(BTN_ROT_UP) == LOW) {
    lastActivity = millis();
    weekOffset--;
    Serial.printf("UP: week offset = %d\n", weekOffset);
    fetchMealImage(weekOffset);
    return true;
  }
  if (digitalRead(BTN_ROT_DN) == LOW) {
    lastActivity = millis();
    weekOffset++;
    Serial.printf("DOWN: week offset = %d\n", weekOffset);
    fetchMealImage(weekOffset);
    return true;
  }
  if (digitalRead(BTN_ROT_OK) == LOW) {
    lastActivity = millis();
    weekOffset = 0;
    Serial.println("OK: refresh current week");
    fetchMealImage(0);
    return true;
  }
  return false;
}

// Active mode: handle buttons until ACTIVE_TIMEOUT inactivity
void activeButtonLoop() {
  Serial.printf("Entering active button mode (%dms timeout)\n", ACTIVE_TIMEOUT);
  lastActivity = millis();

  while (millis() - lastActivity < ACTIVE_TIMEOUT) {
    checkButtons();
    delay(50);
  }

  Serial.println("Button timeout, going to sleep");
}

// ===== WEB SERVER (AP mode only) =====

void handleRoot() {
  String html = "<!DOCTYPE html><html><head>";
  html += "<meta charset='UTF-8'><meta name='viewport' content='width=device-width'>";
  html += "<title>Meal Display Setup</title>";
  html += "<style>";
  html += "body{font-family:sans-serif;max-width:500px;margin:20px auto;padding:20px;background:#f9f9f9;}";
  html += ".card{background:white;padding:20px;border-radius:10px;margin:15px 0;box-shadow:0 2px 4px rgba(0,0,0,.1);}";
  html += "button{background:#4CAF50;color:white;border:none;padding:12px 24px;font-size:16px;border-radius:5px;cursor:pointer;margin:5px 0;}";
  html += "input{width:100%;padding:10px;margin:5px 0;border:1px solid #ddd;border-radius:4px;box-sizing:border-box;}";
  html += "</style></head><body>";

  html += "<div class='card'><h1>Meal Display Setup</h1>";
  html += "<p>Configure WiFi and image server, then the display will enter deep-sleep mode.</p></div>";

  html += "<div class='card'><h2>WiFi</h2>";
  html += "<form action='/wifi' method='POST'>";
  html += "<label>SSID:</label><input name='ssid' required>";
  html += "<label>Password:</label><input name='pass' type='password'>";
  html += "<button type='submit'>Save &amp; Reboot</button></form></div>";

  html += "<div class='card'><h2>Image Server</h2>";
  html += "<form action='/server' method='POST'>";
  html += "<label>Server IP:</label><input name='server_ip' value='" + serverIP + "'>";
  html += "<label>Port:</label><input name='server_port' type='number' value='" + String(serverPort) + "'>";
  html += "<button type='submit'>Save</button></form></div>";

  html += "</body></html>";
  server.send(200, "text/html", html);
}

void handleWiFi() {
  if (server.hasArg("ssid")) {
    prefs.putString("ssid", server.arg("ssid"));
    prefs.putString("pass", server.arg("pass"));
    server.send(200, "text/plain", "WiFi saved! Rebooting...");
    delay(1000);
    ESP.restart();
  }
}

void handleServerConfig() {
  if (server.hasArg("server_ip")) {
    serverIP = server.arg("server_ip");
    serverPort = server.arg("server_port").toInt();
    if (serverPort == 0) serverPort = 5000;
    prefs.putString("srv_ip", serverIP);
    prefs.putInt("srv_port", serverPort);
    server.send(200, "text/plain", "Server saved: " + serverIP + ":" + String(serverPort));
  }
}

void handleReset() {
  prefs.remove("ssid");
  prefs.remove("pass");
  server.send(200, "text/plain", "WiFi reset. Rebooting to AP mode...");
  delay(1000);
  ESP.restart();
}

void runAPMode() {
  WiFi.mode(WIFI_AP);
  WiFi.softAP(AP_SSID, AP_PASS);
  Serial.printf("AP Mode: %s @ %s\n", AP_SSID, WiFi.softAPIP().toString().c_str());
  displayText("Setup Mode", "WiFi: MealDisplay / 12345678", "Open 192.168.4.1");

  server.on("/", handleRoot);
  server.on("/wifi", HTTP_POST, handleWiFi);
  server.on("/server", HTTP_POST, handleServerConfig);
  server.on("/reset", HTTP_GET, handleReset);
  server.begin();

  // Stay awake in AP mode — no deep sleep until WiFi is configured
  while (true) {
    server.handleClient();
    delay(10);
  }
}

// ===== MAIN =====

void setup() {
  Serial.begin(115200);
  delay(500);

  esp_sleep_wakeup_cause_t wakeup = esp_sleep_get_wakeup_cause();
  const char* wakeReason = "Cold boot";
  if (wakeup == ESP_SLEEP_WAKEUP_TIMER) wakeReason = "Timer";
  else if (wakeup == ESP_SLEEP_WAKEUP_EXT1) wakeReason = "Button";
  Serial.printf("\n=== Meal Display [wake: %s] ===\n", wakeReason);

  prefs.begin("meal", false);
  serverIP = prefs.getString("srv_ip", "");
  serverPort = prefs.getInt("srv_port", 5000);

  setupButtons();
  initDisplay();

  // No saved WiFi → AP setup mode (stays awake)
  String savedSSID = prefs.getString("ssid", "");
  if (savedSSID.length() == 0) {
    runAPMode();  // Never returns
  }

  // Connect WiFi
  if (!connectWiFi()) {
    Serial.println("WiFi failed — sleeping 1 hour to retry");
    display.powerOff();
    esp_sleep_enable_timer_wakeup(3600ULL * 1000000ULL);
    uint64_t btnMask = (1ULL << BTN_ROT_UP) | (1ULL << BTN_ROT_DN) | (1ULL << BTN_ROT_OK);
    esp_sleep_enable_ext1_wakeup(btnMask, ESP_EXT1_WAKEUP_ANY_LOW);
    esp_deep_sleep_start();
  }

  // Sync time
  syncTime();

  // If a button woke us, apply its action to the initial fetch so the wake-press
  // itself counts as navigation (otherwise the first press is "lost" to waking up).
  weekOffset = 0;
  if (wakeup == ESP_SLEEP_WAKEUP_EXT1) {
    uint64_t pinMask = esp_sleep_get_ext1_wakeup_status();
    if (pinMask & (1ULL << BTN_ROT_UP))      weekOffset = -1;
    else if (pinMask & (1ULL << BTN_ROT_DN)) weekOffset =  1;
    // BTN_ROT_OK → offset stays 0 (refresh current week)
    Serial.printf("Wake button mask=0x%llx → initial offset=%d\n", pinMask, weekOffset);
  }
  if (!fetchMealImage(weekOffset)) {
    Serial.println("Fetch failed");
  }

  // If woken by button → enter active mode (handle more button presses)
  if (wakeup == ESP_SLEEP_WAKEUP_EXT1) {
    activeButtonLoop();
  }

  // Go to deep sleep
  enterDeepSleep();
}

void loop() {
  // Never reached — we sleep from setup()
}
