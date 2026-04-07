from flask import Flask, request, jsonify, render_template
from blueprint_parser import parse_blueprint
from grid_generator import generate_grid
from astar import astar, smooth_path, path_to_commands
import os
import uuid
import numpy as np



app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# In-memory store
state = {
    "grid": None,
    "rooms": [],
    "orientation": None,
    "parse_status": None,
    "robot_pose": {"row": 0, "col": 0, "heading": "N"},
    "command_queue": [],
    "blueprint_path": None,
    "grid_size": None,
}

@app.route("/")
def index():
    return render_template("index.html")

# ── ROUTE 1: Upload blueprint ──────────────────────────────────────────────
@app.route("/upload", methods=["POST"])
def upload():
    try:
        if "blueprint" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["blueprint"]
        filename = f"{uuid.uuid4().hex}.jpg"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        print(f"✅ File saved: {filepath}")

        print("🔍 Starting OCR parse...")
        result = parse_blueprint(filepath)
        print(f"✅ Parse done: {result['parse_status']}")

        print("🗺️  Generating grid...")
        grid, gw, gh = generate_grid(filepath, cell_size=10)
        print(f"✅ Grid done: {gw}x{gh}")

        state["grid"] = grid.tolist()
        state["rooms"] = result.get("rooms", [])
        state["orientation"] = result.get("orientation")
        state["parse_status"] = result.get("parse_status")
        state["blueprint_path"] = filepath
        state["grid_size"] = {"rows": gh, "cols": gw}

        return jsonify({
            "parse_status": result["parse_status"],
            "orientation": result.get("orientation"),
            "rooms": result.get("rooms", []),
            "missing_fields": result.get("missing_fields", []),
            "grid_size": {"rows": gh, "cols": gw},
        })

    except Exception as e:
        import traceback
        print("❌ ERROR in /upload:")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ── ROUTE 2: Set robot start pose ─────────────────────────────────────────
@app.route("/set-pose", methods=["POST"])
def set_pose():
    try:
        data = request.json
        state["robot_pose"] = {
            "row":     data.get("row", 0),
            "col":     data.get("col", 0),
            "heading": data.get("heading", "N"),
        }
        return jsonify({"ok": True, "pose": state["robot_pose"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── ROUTE 3: Navigate to a room ───────────────────────────────────────────
@app.route("/navigate", methods=["POST"])
def navigate():
    try:
        if state["grid"] is None:
            return jsonify({"error": "No blueprint loaded. Call /upload first."}), 400

        data = request.json
        target_room = data.get("room", "").strip().lower()

        grid = np.array(state["grid"], dtype=np.uint8)

        # Match room name (partial match)
        matched = None
        for room in state["rooms"]:
            if target_room in room["name"].lower():
                matched = room
                break

        if not matched:
            available = [r["name"] for r in state["rooms"]]
            return jsonify({
                "error": f"Room '{target_room}' not found",
                "available_rooms": available
            }), 404

        # Convert room center pixel (x, y) → grid (row, col)
        # Convert room center pixel (x, y) → grid (row, col)
        cell_size = 10
        rx, ry = matched["center"]
        goal_col = rx // cell_size
        goal_row = ry // cell_size

        # Snap goal to nearest free cell (doorway)
        # Instead of room center (which may be inside walls after inflation),
        # find the nearest traversable cell to the room center
        grid_np = np.array(state["grid"], dtype=np.uint8)
        grid = grid_np
        if grid_np[goal_row][goal_col] == 1:
            # Search outward from room center for nearest free cell
            found = False
            for radius in range(1, 20):
                for dr in range(-radius, radius+1):
                    for dc in range(-radius, radius+1):
                        nr, nc = goal_row + dr, goal_col + dc
                        if 0 <= nr < grid_np.shape[0] and \
                           0 <= nc < grid_np.shape[1] and \
                           grid_np[nr][nc] == 0:
                            goal_row, goal_col = nr, nc
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            print(f"   Snapped goal to free cell: ({goal_row}, {goal_col})")

        start_row = state["robot_pose"]["row"]
        start_col = state["robot_pose"]["col"]
        heading   = state["robot_pose"]["heading"]

        start = (start_row, start_col)
        goal  = (goal_row, goal_col)

        print(f"🔍 Navigating to {matched['name']}: {start} → {goal}")

        path = astar(grid, start, goal)

        if not path:
            return jsonify({"error": "No path found to target room"}), 400

        smoothed = smooth_path(path, grid)
        commands = path_to_commands(path, cell_size_cm=10,
                                    initial_heading=heading)

        state["command_queue"] = commands
        state["current_path"] = path

        print(f"✅ Path found: {len(path)} cells, {len(commands)} commands")

        return jsonify({
            "target_room": matched["name"],
            "start": list(start),
            "goal": list(goal),
            "path_cells": len(path),
            "commands": commands,
        })

    except Exception as e:
        import traceback
        print("❌ ERROR in /navigate:")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ── ROUTE 4: Get current status ────────────────────────────────────────────
@app.route("/status", methods=["GET"])
def status():
    return jsonify({
        "parse_status":   state["parse_status"],
        "robot_pose":     state["robot_pose"],
        "rooms_detected": len(state["rooms"]),
        "command_queue":  state["command_queue"],
        "grid_loaded":    state["grid"] is not None,
    })

# ── ROUTE 5: ESP32 polls this for next command ─────────────────────────────
@app.route("/get-command", methods=["GET"])
def get_command():
    if state["command_queue"]:
        cmd = state["command_queue"].pop(0)
        return jsonify({
            "command":   cmd,
            "remaining": len(state["command_queue"])
        })
    return jsonify({"command": "DONE", "remaining": 0})

# ── MAIN ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 Flask server starting on http://localhost:5000")
    print("Routes:")
    print("  POST /upload      — upload blueprint image")
    print("  POST /set-pose    — set robot start position")
    print("  POST /navigate    — navigate to a room")
    print("  GET  /status      — get system status")
    print("  GET  /get-command — ESP32 polls for next command")
    app.run(debug=True, port=8080)