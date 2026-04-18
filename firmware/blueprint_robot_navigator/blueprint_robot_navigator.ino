#include <AccelStepper.h>

// Pins
#define L_IN1 13
#define L_IN2 12
#define L_IN3 14
#define L_IN4 27

#define R_IN1 26
#define R_IN2 23
#define R_IN3 25
#define R_IN4 32

float maxSpeedVal = 2000;
float setSpeedVal = 1000;

AccelStepper leftMotor(AccelStepper::HALF4WIRE, L_IN1, L_IN3, L_IN2, L_IN4);
AccelStepper rightMotor(AccelStepper::HALF4WIRE, R_IN1, R_IN3, R_IN2, R_IN4);

void setup() {
  Serial.begin(115200);
  leftMotor.setMaxSpeed(maxSpeedVal);
  rightMotor.setMaxSpeed(maxSpeedVal);

  Serial.println("Enter command: f b l r");
}

void loop() {

  if (Serial.available()) {
    char cmd = Serial.read();
    Serial.println(cmd);

    if (cmd == 'f') moveForTime(setSpeedVal, -setSpeedVal, 2000);
    if (cmd == 'b') moveForTime(-setSpeedVal, setSpeedVal, 2000);
    if (cmd == 'l') moveForTime(-setSpeedVal, -setSpeedVal, 1500);
    if (cmd == 'r') moveForTime(setSpeedVal, setSpeedVal, 1500);
  }
}

// 🔥 THIS is the movement loop you expected
void moveForTime(float leftSpeed, float rightSpeed, int duration) {

  leftMotor.setSpeed(leftSpeed);
  rightMotor.setSpeed(rightSpeed);

  unsigned long start = millis();

  while (millis() - start < duration) {
    leftMotor.runSpeed();
    rightMotor.runSpeed();
  }

  // STOP after movement
  leftMotor.setSpeed(0);
  rightMotor.setSpeed(0);
}