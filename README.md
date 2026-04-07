# Blueprint Navigator

This repository now includes a v1-compatible layout for the implementation plan you shared:

- `backend/app/main.py`: FastAPI app entrypoint
- `backend/app/api/routes.py`: parsing, pose, navigation, robot status, and session routes
- `backend/app/models/schemas.py`: shared contracts
- `backend/app/services/*.py`: OCR, parser, planner, session state, and optional Firebase publishing
- `frontend/*`: static browser dashboard
- `firmware/blueprint_robot_navigator.ino`: ESP32 scaffold

## Current Status

This is a compatibility migration from the earlier Flask prototype. It now matches the requested structure much more closely, but some advanced behaviors still need real-world tuning and hardware validation.

Implemented now:

- FastAPI backend structure
- typed schemas
- session state
- parse endpoint
- pose endpoint
- navigation endpoint
- manual override endpoint
- robot status endpoint
- static frontend
- Firebase configuration hooks
- initial tests
- firmware scaffold

Still needing validation:

- OCR quality on real blueprint samples
- doorway detection quality
- Firebase round-trip testing
- full ESP32 queue execution

## Backend Setup

Use Python 3.12.

```powershell
py -3.12 -m pip install -r backend\requirements.txt
py -3.12 -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8080
```

Then open `http://localhost:8080`.

## Firebase Setup

Set these environment variables before starting the backend:

- `FIREBASE_CREDENTIALS`
- `FIREBASE_DATABASE_URL`
- `FIREBASE_PROJECT_ID`

See `backend/.env.example`.

## What I Still Need From You

To fully wire and test Firebase, I need:

- your Firebase Realtime Database URL
- a Firebase Admin SDK service-account JSON file path, or the JSON itself
- the robot ID convention you want to use

To improve parser quality, I also need:

- 2-5 real blueprint samples
- the usual unit system on those blueprints
- one example of a room label and dimension pair from a real image

## Firebase Notes

The values you shared for the web app config and RTDB URL are now reflected in `backend/.env.example` and the firmware scaffold.

Important distinction:

- the web config (`apiKey`, `appId`, `projectId`, RTDB URL) is enough for browser-side Firebase setup and ESP32 client setup
- the FastAPI backend still needs a Firebase Admin service account JSON to use `firebase-admin` for queue/status writes

If you want, the next step is to provide that Admin SDK JSON and I can wire the backend to your live Firebase project directly.
