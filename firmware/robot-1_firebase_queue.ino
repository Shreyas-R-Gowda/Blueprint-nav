#include <WiFi.h>
#include <AccelStepper.h>
#include <Firebase_ESP_Client.h>

// robot-1 queue-driven firmware for the Blueprint Navigator project.
// This version removes Blynk and consumes Firebase RTDB queue items one at a time.

#define WIFI_SSID "Shree"
#define WIFI_PASSWORD "!!!!!!!!"

#define FIREBASE_API_KEY "AIzaSyAltzaCaE4QI8FWW0m9ZCW8mPdUer3QVCk"
#define FIREBASE_DATABASE_URL "https://id-project-b4d10-default-rtdb.asia-southeast1.firebasedatabase.app/"
#define ROBOT_ID "robot-1"

// If anonymous auth is not enabled in Firebase Authentication,
// replace this with email/password auth or a supported token flow.
#define FIREBASE_USER_EMAIL ""
#define FIREBASE_USER_PASSWORD ""

#define TRIG_PIN 18
#define ECHO_PIN 19

#define MOTOR_INTERFACE_TYPE 4
AccelStepper leftMotor(MOTOR_INTERFACE_TYPE, 13, 14, 12, 27);
AccelStepper rightMotor(MOTOR_INTERFACE_TYPE, 26, 23, 25, 32);

FirebaseData fbdo;
FirebaseAuth auth;
FirebaseConfig config;

const float WHEEL_DIAMETER_CM = 4.9f;
const float WHEEL_BASE_CM = 8.0f;
const float STEPS_PER_REV = 2048.0f;
const float CM_PER_STEP = (PI * WHEEL_DIAMETER_CM) / STEPS_PER_REV;
const float OBSTACLE_THRESHOLD_CM = 10.0f;

float poseX = 0.0f;
float poseY = 0.0f;
String heading = "N";

String activeSequence = "";
String activeCommand = "";
bool commandLoaded = false;
bool motionInProgress = false;
bool forwardBlocked = false;

unsigned long lastQueuePollMs = 0;
unsigned long lastHeartbeatMs = 0;

struct ParsedCommand {
  char type;
  float value;
  bool hasUnit;
};

String queuePath() {
  return String("/robots/") + ROBOT_ID + "/queue";
}

String metaPath() {
  return String("/robots/") + ROBOT_ID + "/meta";
}

String statusPath() {
  return String("/robots/") + ROBOT_ID + "/status";
}

String itemPath(const String& sequence) {
  return queuePath() + "/" + sequence;
}

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("WiFi connected. IP: ");
  Serial.println(WiFi.localIP());
}

void connectFirebase() {
  config.api_key = FIREBASE_API_KEY;
  config.database_url = FIREBASE_DATABASE_URL;

  Firebase.begin(&config, &auth);
  Firebase.reconnectWiFi(true);

  if (strlen(FIREBASE_USER_EMAIL) > 0 && strlen(FIREBASE_USER_PASSWORD) > 0) {
    if (!Firebase.signUp(&config, &auth, FIREBASE_USER_EMAIL, FIREBASE_USER_PASSWORD)) {
      Serial.printf("Firebase signUp failed: %s\n", config.signer.signupError.message.c_str());
    }
  } else {
    if (!Firebase.signUp(&config, &auth, "", "")) {
      Serial.printf("Anonymous Firebase signUp failed: %s\n", config.signer.signupError.message.c_str());
    }
  }
}

float readUltrasonicDistance() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  float duration = pulseIn(ECHO_PIN, HIGH, 30000);
  float distance = duration * 0.034f / 2.0f;
  if (distance == 0.0f || distance > 400.0f) {
    return 999.0f;
  }
  return distance;
}

void stopMotors() {
  leftMotor.stop();
  rightMotor.stop();
  leftMotor.runToPosition();
  rightMotor.runToPosition();
  motionInProgress = false;
}

String normalizeHeading(const String& value) {
  if (value == "N" || value == "E" || value == "S" || value == "W") {
    return value;
  }
  return "N";
}

void advanceHeadingRight(int degrees) {
  int turns = ((degrees % 360) + 360) % 360 / 90;
  for (int i = 0; i < turns; i++) {
    if (heading == "N") heading = "E";
    else if (heading == "E") heading = "S";
    else if (heading == "S") heading = "W";
    else heading = "N";
  }
}

void advanceHeadingLeft(int degrees) {
  int turns = ((degrees % 360) + 360) % 360 / 90;
  for (int i = 0; i < turns; i++) {
    if (heading == "N") heading = "W";
    else if (heading == "W") heading = "S";
    else if (heading == "S") heading = "E";
    else heading = "N";
  }
}

void updatePoseLinear(float distanceCm) {
  if (heading == "N") poseY -= distanceCm;
  else if (heading == "S") poseY += distanceCm;
  else if (heading == "E") poseX += distanceCm;
  else if (heading == "W") poseX -= distanceCm;
}

void publishRobotStatus(const String& queueStatus, const String& obstacleState, float progress) {
  if (!Firebase.ready()) {
    return;
  }

  FirebaseJson statusJson;
  statusJson.set("current_command", activeCommand);
  statusJson.set("queue_status", queueStatus);
  statusJson.set("obstacle_state", obstacleState);
  statusJson.set("progress", progress);
  statusJson.set("pose/x", poseX);
  statusJson.set("pose/y", poseY);
  statusJson.set("pose/heading", heading);
  statusJson.set("last_heartbeat", (int) (millis() / 1000));

  Firebase.RTDB.updateNode(&fbdo, statusPath(), &statusJson);
}

bool updateQueueItemStatus(const String& sequence, const String& status, float progress) {
  if (!Firebase.ready()) {
    return false;
  }

  FirebaseJson itemJson;
  itemJson.set("status", status);
  itemJson.set("progress", progress);
  return Firebase.RTDB.updateNode(&fbdo, itemPath(sequence), &itemJson);
}

bool parseCommand(const String& raw, ParsedCommand& parsed) {
  String command = raw;
  command.trim();
  command.toUpperCase();

  if (command == "STOP") {
    parsed.type = 'X';
    parsed.value = 0.0f;
    parsed.hasUnit = false;
    return true;
  }

  if (command.length() < 2) {
    return false;
  }

  char type = command.charAt(0);
  if (type != 'F' && type != 'B' && type != 'L' && type != 'R') {
    return false;
  }

  String numericPart = "";
  for (int i = 1; i < command.length(); i++) {
    char ch = command.charAt(i);
    if ((ch >= '0' && ch <= '9') || ch == '.') {
      numericPart += ch;
    } else {
      break;
    }
  }

  if (numericPart.length() == 0) {
    return false;
  }

  parsed.type = type;
  parsed.value = numericPart.toFloat();
  parsed.hasUnit = command.endsWith("CM");
  return true;
}

void startStraightMotion(float distanceCm) {
  long steps = labs((long) (distanceCm / CM_PER_STEP));
  leftMotor.setCurrentPosition(0);
  rightMotor.setCurrentPosition(0);

  if (distanceCm >= 0.0f) {
    leftMotor.move(-steps);
    rightMotor.move(steps);
  } else {
    leftMotor.move(steps);
    rightMotor.move(-steps);
  }

  motionInProgress = true;
}

void startTurnMotion(float degrees) {
  float radians = degrees * PI / 180.0f;
  float turnDistance = (WHEEL_BASE_CM / 2.0f) * fabs(radians);
  long steps = (long) (turnDistance / CM_PER_STEP);

  leftMotor.setCurrentPosition(0);
  rightMotor.setCurrentPosition(0);

  if (degrees > 0.0f) {
    leftMotor.move(-steps);
    rightMotor.move(-steps);
  } else {
    leftMotor.move(steps);
    rightMotor.move(steps);
  }

  motionInProgress = true;
}

void markCommandDone() {
  updateQueueItemStatus(activeSequence, "DONE", 1.0f);
  publishRobotStatus("DONE", "CLEAR", 1.0f);
  activeSequence = "";
  activeCommand = "";
  commandLoaded = false;
  forwardBlocked = false;
}

void handleStopCommand() {
  stopMotors();
  updateQueueItemStatus(activeSequence, "DONE", 1.0f);
  publishRobotStatus("DONE", "CLEAR", 1.0f);
  activeSequence = "";
  activeCommand = "";
  commandLoaded = false;
}

bool startCommandExecution(const String& sequence, const String& command) {
  ParsedCommand parsed;
  if (!parseCommand(command, parsed)) {
    updateQueueItemStatus(sequence, "ERROR", 0.0f);
    publishRobotStatus("ERROR", "CLEAR", 0.0f);
    return false;
  }

  activeSequence = sequence;
  activeCommand = command;
  commandLoaded = true;
  forwardBlocked = false;

  updateQueueItemStatus(sequence, "IN_PROGRESS", 0.0f);
  publishRobotStatus("IN_PROGRESS", "CLEAR", 0.0f);

  if (parsed.type == 'X') {
    handleStopCommand();
    return true;
  }

  if (parsed.type == 'F') {
    startStraightMotion(parsed.value);
  } else if (parsed.type == 'B') {
    startStraightMotion(-parsed.value);
  } else if (parsed.type == 'L') {
    startTurnMotion(-parsed.value);
  } else if (parsed.type == 'R') {
    startTurnMotion(parsed.value);
  }

  return true;
}

void finalizeMotionFromCommand() {
  ParsedCommand parsed;
  if (!parseCommand(activeCommand, parsed)) {
    updateQueueItemStatus(activeSequence, "ERROR", 0.0f);
    publishRobotStatus("ERROR", "CLEAR", 0.0f);
    activeSequence = "";
    activeCommand = "";
    commandLoaded = false;
    return;
  }

  if (parsed.type == 'F') {
    updatePoseLinear(parsed.value);
  } else if (parsed.type == 'B') {
    updatePoseLinear(-parsed.value);
  } else if (parsed.type == 'L') {
    advanceHeadingLeft((int) parsed.value);
  } else if (parsed.type == 'R') {
    advanceHeadingRight((int) parsed.value);
  }

  markCommandDone();
}

String findNextSequence(FirebaseJson* json) {
  size_t len = json->iteratorBegin();
  String bestSequence = "";
  int bestValue = 2147483647;

  for (size_t i = 0; i < len; i++) {
    int type = 0;
    String key;
    String value;
    json->iteratorGet(i, type, key, value);
    if (type != FirebaseJson::JSON_OBJECT) {
      continue;
    }

    FirebaseJsonData itemData;
    json->get(itemData, key + "/status");
    String status = itemData.success ? itemData.stringValue : "";
    if (status != "PENDING" && status != "IN_PROGRESS" && status != "BLOCKED") {
      continue;
    }

    int sequenceNumber = key.toInt();
    if (sequenceNumber > 0 && sequenceNumber < bestValue) {
      bestValue = sequenceNumber;
      bestSequence = key;
    }
  }

  json->iteratorEnd();
  return bestSequence;
}

void pollQueue() {
  if (!Firebase.ready() || commandLoaded || millis() - lastQueuePollMs < 1200) {
    return;
  }
  lastQueuePollMs = millis();

  if (!Firebase.RTDB.getJSON(&fbdo, queuePath())) {
    return;
  }
  if (fbdo.dataType() != "json") {
    return;
  }

  FirebaseJson* json = fbdo.to<FirebaseJson*>();
  if (json == nullptr) {
    return;
  }

  String sequence = findNextSequence(json);
  if (sequence.length() == 0) {
    return;
  }

  FirebaseJsonData commandData;
  json->get(commandData, sequence + "/command");
  if (!commandData.success) {
    return;
  }

  String command = commandData.stringValue;
  command.trim();
  if (command.length() == 0) {
    return;
  }

  Serial.print("Executing queue item #");
  Serial.print(sequence);
  Serial.print(": ");
  Serial.println(command);
  startCommandExecution(sequence, command);
}

void maintainActiveMotion() {
  if (!commandLoaded) {
    return;
  }

  ParsedCommand parsed;
  if (!parseCommand(activeCommand, parsed)) {
    return;
  }

  if (parsed.type == 'F') {
    float obstacleDistance = readUltrasonicDistance();
    if (obstacleDistance <= OBSTACLE_THRESHOLD_CM) {
      if (!forwardBlocked) {
        stopMotors();
        forwardBlocked = true;
        updateQueueItemStatus(activeSequence, "BLOCKED", 0.0f);
        publishRobotStatus("BLOCKED", "FRONT_OBSTACLE", 0.0f);
        Serial.println("Forward motion blocked by obstacle.");
      }
      return;
    }

    if (forwardBlocked && !motionInProgress) {
      startStraightMotion(parsed.value);
      forwardBlocked = false;
      updateQueueItemStatus(activeSequence, "IN_PROGRESS", 0.0f);
      publishRobotStatus("IN_PROGRESS", "CLEAR", 0.0f);
      Serial.println("Obstacle cleared. Resuming forward motion.");
    }
  }

  if (!motionInProgress) {
    return;
  }

  leftMotor.run();
  rightMotor.run();

  if (leftMotor.distanceToGo() == 0 && rightMotor.distanceToGo() == 0) {
    stopMotors();
    finalizeMotionFromCommand();
  }
}

void publishHeartbeat() {
  if (millis() - lastHeartbeatMs < 3000) {
    return;
  }
  lastHeartbeatMs = millis();

  String queueStatus = "IDLE";
  String obstacleState = "CLEAR";
  float progress = 0.0f;

  if (commandLoaded && forwardBlocked) {
    queueStatus = "BLOCKED";
    obstacleState = "FRONT_OBSTACLE";
  } else if (commandLoaded) {
    queueStatus = "IN_PROGRESS";
  }

  publishRobotStatus(queueStatus, obstacleState, progress);
}

void setup() {
  Serial.begin(115200);

  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);

  leftMotor.setMaxSpeed(800);
  leftMotor.setAcceleration(800);
  rightMotor.setMaxSpeed(800);
  rightMotor.setAcceleration(800);
  stopMotors();

  connectWiFi();
  connectFirebase();
  publishRobotStatus("IDLE", "CLEAR", 0.0f);

  Serial.println("robot-1 firmware ready.");
}

void loop() {
  pollQueue();
  maintainActiveMotion();
  publishHeartbeat();
}
