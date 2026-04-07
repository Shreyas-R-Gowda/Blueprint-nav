#include <WiFi.h>
#include <AccelStepper.h>
#include <Firebase_ESP_Client.h>

const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* ROBOT_ID = "robot-1";
const char* FIREBASE_API_KEY = "YOUR_FIREBASE_WEB_API_KEY";
const char* FIREBASE_DATABASE_URL = "https://id-project-b4d10-default-rtdb.asia-southeast1.firebasedatabase.app/";

const int FRONT_TRIG_PIN = 18;
const int FRONT_ECHO_PIN = 19;

const int LEFT_MOTOR_PINS[4] = {13, 14, 12, 27};
const int RIGHT_MOTOR_PINS[4] = {26, 23, 25, 32};

const float WHEEL_DIAMETER_CM = 4.9f;
const float WHEEL_BASE_CM = 8.0f;
const int STEPS_PER_REVOLUTION = 2048;

void setup() {
  Serial.begin(115200);
  pinMode(FRONT_TRIG_PIN, OUTPUT);
  pinMode(FRONT_ECHO_PIN, INPUT);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("Firmware scaffold ready.");
  Serial.println("Firebase RTDB URL placeholder is configured.");
  Serial.println("Next step: add Firebase auth, queue polling, and motor execution.");
}

void loop() {
  // Planned runtime flow:
  // 1. Poll /robots/<robot_id>/queue for the next actionable command.
  // 2. Mark it IN_PROGRESS.
  // 3. Execute F/B/L/R/STOP.
  // 4. If a forward obstacle is detected, stop, publish BLOCKED, wait, then resume.
  // 5. Mark DONE and continue.
  delay(1000);
}
