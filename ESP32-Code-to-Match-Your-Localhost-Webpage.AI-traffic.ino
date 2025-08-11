#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <TM1637Display.h>

// WiFi credentials
const char* ssid = "V-DSL Modem";
const char* password = "TimeisMoneyqkzee123";

// Server details
const char* serverUrl = "http://192.168.10.9:5000/traffic_data";

// DNS configuration
IPAddress dns(192, 168, 10, 1);

// LED pins for 4 lanes (Red, Yellow, Green)
const int lanePins[4][3] = {
  {23, 22, 21},  // Lane 1
  {19, 18, 5},   // Lane 2
  {17, 16, 4},   // Lane 3
  {15, 2, 13}    // Lane 4
};

// TM1637 display connections (CLK, DIO)
const int displayPins[4][2] = {
  {32, 33},  // Lane 1 display
  {25, 26},  // Lane 2 display
  {27, 14},  // Lane 3 display
  {12, 34}   // Lane 4 display (Note: Pin 13 is also a LED pin)
};

// Create display objects
TM1637Display laneDisplays[4] = {
  TM1637Display(displayPins[0][0], displayPins[0][1]),
  TM1637Display(displayPins[1][0], displayPins[1][1]),
  TM1637Display(displayPins[2][0], displayPins[2][1]),
  TM1637Display(displayPins[3][0], displayPins[3][1])
};

// Traffic light states
String currentLights[4] = {"red", "red", "red", "red"};
int remainingTimes[4] = {0, 0, 0, 0};
unsigned long lastUpdateTime = 0;
const long updateInterval = 1000;

void setup() {
  Serial.begin(115200);
  delay(1000);
  
  Serial.println("\nESP32 Traffic Controller with TM1637 Displays");
  Serial.println("-------------------------------------------");
  
  // Initialize LEDs
  for (int lane = 0; lane < 4; lane++) {
    for (int light = 0; light < 3; light++) {
      pinMode(lanePins[lane][light], OUTPUT);
      digitalWrite(lanePins[lane][light], LOW);
    }
    setLight(lane, 'r'); // Start all red
  }

  // Initialize displays
  for (int lane = 0; lane < 4; lane++) {
    laneDisplays[lane].setBrightness(0x0f); // Maximum brightness
    laneDisplays[lane].clear();
    laneDisplays[lane].showNumberDec(0); // Start with 0
  }

  // Connect to WiFi
  WiFi.config(INADDR_NONE, INADDR_NONE, dns);
  WiFi.begin(ssid, password);
  
  Serial.println("Connecting to WiFi...");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    // Blink displays while connecting
    for (int lane = 0; lane < 4; lane++) {
      laneDisplays[lane].clear();
      delay(50);
      laneDisplays[lane].showNumberDec(lane+1);
    }
  }
  
  Serial.println("\nConnected!");
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());
  Serial.print("Server URL: ");
  Serial.println(serverUrl);
}

void loop() {
  if (millis() - lastUpdateTime >= updateInterval) {
    if (WiFi.status() == WL_CONNECTED) {
      fetchTrafficData();
    } else {
      Serial.println("WiFi disconnected. Reconnecting...");
      WiFi.reconnect();
    }
    lastUpdateTime = millis();
  }
  
  // Update displays continuously
  updateDisplays();
}

void fetchTrafficData() {
  HTTPClient http;
  
  Serial.println("\n--- Fetching Data ---");
  Serial.print("Connecting to: ");
  Serial.println(serverUrl);
  
  http.begin(serverUrl);
  int httpCode = http.GET();
  
  if (httpCode == HTTP_CODE_OK) {
    String payload = http.getString();
    Serial.println("\nRaw Server Response:");
    Serial.println(payload);
    processTrafficData(payload);
  } else {
    Serial.print("HTTP Error: ");
    Serial.println(http.errorToString(httpCode));
    networkDebugInfo();
    
    // Show error on displays (E.rr)
    for (int lane = 0; lane < 4; lane++) {
      laneDisplays[lane].clear();
      laneDisplays[lane].showNumberDecEx(httpCode, 0b01000000, true);
    }
  }
  
  http.end();
}

void processTrafficData(String jsonData) {
  DynamicJsonDocument doc(2048);
  DeserializationError error = deserializeJson(doc, jsonData);
  
  if (error) {
    Serial.print("JSON Error: ");
    Serial.println(error.c_str());
    
    // Show error on displays (J.SON)
    for (int lane = 0; lane < 4; lane++) {
      laneDisplays[lane].clear();
      laneDisplays[lane].showNumberDecEx(5050, 0b01000000, true);
    }
    return;
  }

  Serial.println("\nProcessed Data:");
  Serial.println("--------------");
  
  for (int lane = 0; lane < 4; lane++) {
    String laneKey = "lane" + String(lane+1);
    JsonObject laneData = doc[laneKey];
    
    String newState = laneData["light"].as<String>();
    bool emergency = laneData["emergency"];
    int vehicles = laneData["vehicle_count"];
    int weight = laneData["weight"];
    remainingTimes[lane] = laneData["remaining_time"];
    
    Serial.printf("Lane %d:\n", lane+1);
    Serial.printf("  State: %s\n", newState.c_str());
    Serial.printf("  Emergency: %s\n", emergency ? "YES" : "NO");
    Serial.printf("  Vehicles: %d\n", vehicles);
    Serial.printf("  Weight: %d\n", weight);
    Serial.printf("  Time Left: %ds\n", remainingTimes[lane]);
    
    if (newState != currentLights[lane]) {
      Serial.printf("  >> Changing to %s\n", newState.c_str());
      setLight(lane, newState.charAt(0));
      currentLights[lane] = newState;
      
      // Flash display when state changes
      laneDisplays[lane].clear();
      delay(100);
      laneDisplays[lane].showNumberDec(remainingTimes[lane]);
    }
    Serial.println();
  }
}

void updateDisplays() {
  static unsigned long lastBlinkTime = 0;
  static bool blinkState = false;
  
  // Blink logic for emergency/critical times
  if (millis() - lastBlinkTime >= 500) {
    blinkState = !blinkState;
    lastBlinkTime = millis();
  }
  
  for (int lane = 0; lane < 4; lane++) {
    bool shouldBlink = (remainingTimes[lane] <= 5) || currentLights[lane].equals("yellow");
    
    if (shouldBlink && blinkState) {
      laneDisplays[lane].clear();
    } else {
      laneDisplays[lane].showNumberDec(remainingTimes[lane]);
    }
  }
}

void setLight(int lane, char state) {
  // Turn off all lights first
  for (int i = 0; i < 3; i++) {
    digitalWrite(lanePins[lane][i], LOW);
  }
  
  // Turn on the correct light
  switch (state) {
    case 'r': digitalWrite(lanePins[lane][0], HIGH); break;
    case 'y': digitalWrite(lanePins[lane][1], HIGH); break;
    case 'g': digitalWrite(lanePins[lane][2], HIGH); break;
  }
}

void networkDebugInfo() {
  Serial.println("\nNetwork Debug Info:");
  Serial.println("------------------");
  Serial.print("WiFi Status: ");
  Serial.println(WiFi.status());
  Serial.print("SSID: ");
  Serial.println(WiFi.SSID());
  Serial.print("RSSI: ");
  Serial.println(WiFi.RSSI());
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());
  Serial.print("Subnet Mask: ");
  Serial.println(WiFi.subnetMask());
  Serial.print("Gateway IP: ");
  Serial.println(WiFi.gatewayIP());
  Serial.print("DNS Server: ");
  Serial.println(WiFi.dnsIP());
}