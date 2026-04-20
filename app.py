from flask import Flask, request, jsonify, render_template
from blueprint_parser import parse_blueprint
from grid_generator import generate_grid, inflate_obstacles
from astar import astar, smooth_path, path_to_commands
from firebase_queue import firebase_queue
import os
import uuid
import math
import numpy as np

ROBOT_ID = "wheelchair_01"

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
    "command_queue": [],        # remaining commands (pops as ESP32 polls)
    "all_commands": [],         # full list — never popped, used for sequence tracking
    "blueprint_path": None,
    "grid_size": None,
    "real_cm_per_cell": 10.0,
    "robot_params": {"robot_width_cm": 60.0, "safety_margin_cm": 10.0},
    "robot_id": ROBOT_ID,
}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/favicon.ico")
def favicon():
    return "", 204

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

        unit           = request.form.get("unit", "cm")
        wall_thickness = round(float(request.form.get("wall_thickness", 4)))
        robot_width_cm = float(request.form.get("robot_width", 60.0))
        safety_margin  = float(request.form.get("safety_margin", 10.0))
        building_width = request.form.get("building_width")
        building_width = float(building_width) if building_width else None

        # Fallback: use OCR-estimated building width when user didn't provide one
        est_dims = result.get("estimated_building_dims")
        if building_width is None and est_dims and est_dims.get("width_cm"):
            building_width = float(est_dims["width_cm"])
            print(f"   Using OCR-estimated building width: {building_width} {unit}")

        print("🗺️  Generating grid...")
        grid, gw, gh, real_cm_per_cell, cell_size_px, scale_applied = generate_grid(
            filepath, cell_size=10,
            wall_thickness=wall_thickness,
            unit=unit,
            robot_width_cm=robot_width_cm,
            safety_margin_cm=safety_margin,
            building_width_units=building_width,
        )
        print(f"✅ Grid done: {gw}x{gh}, cell={cell_size_px}px, {real_cm_per_cell:.2f} cm/cell")

        if scale_applied:
            # Real scale known — compute inflation from physical dimensions.
            robot_width_cells   = max(1, round(robot_width_cm / 2.0 / real_cm_per_cell))
            safety_margin_cells = max(0, math.floor(safety_margin / real_cm_per_cell))
        else:
            # Unknown scale — use 1-cell minimal inflation to avoid sealing doorways.
            robot_width_cells   = 1
            safety_margin_cells = 0
        inflation_cells = robot_width_cells + safety_margin_cells
        inflated = inflate_obstacles(grid,
                                     robot_width_cells=robot_width_cells,
                                     safety_margin_cells=safety_margin_cells)
        print(f"   Inflation: {inflation_cells} cells "
              f"({'real scale' if scale_applied else 'fallback 10 cm/cell'})")

        state["grid"]          = inflated.tolist()
        state["rooms"]         = result.get("rooms", [])
        state["orientation"]   = result.get("orientation")
        state["parse_status"]  = result.get("parse_status")
        state["blueprint_path"]= filepath
        state["grid_size"]     = {"rows": gh, "cols": gw}
        state["real_cm_per_cell"] = real_cm_per_cell
        state["cell_size_px"]  = cell_size_px
        state["robot_params"]  = {
            "robot_width_cm": robot_width_cm,
            "safety_margin_cm": safety_margin,
        }
        # Clear any stale commands from a previous navigation
        state["command_queue"] = []
        state["all_commands"]  = []

        # ── Firebase: reset robot state on new blueprint ──────────────────
        try:
            firebase_queue.cancel_queue(ROBOT_ID)
            firebase_queue.publish_status(ROBOT_ID, {
                "row": None, "col": None, "heading": None
            })
        except Exception as fb_err:
            print(f"   ⚠️  Firebase reset failed: {fb_err}")

        return jsonify({
            "parse_status":           result["parse_status"],
            "orientation":            result.get("orientation"),
            "rooms":                  result.get("rooms", []),
            "missing_fields":         result.get("missing_fields", []),
            "grid_size":              {"rows": gh, "cols": gw},
            "real_cm_per_cell":       real_cm_per_cell,
            "cell_size_px":           cell_size_px,
            "inflation_cells":        inflation_cells,
            "estimated_building_dims": est_dims,
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
        row     = data.get("row", 0)
        col     = data.get("col", 0)
        heading = data.get("heading", "N")
        state["robot_pose"] = {"row": row, "col": col, "heading": heading}

        # ── Firebase: sync pose ───────────────────────────────────────────
        try:
            firebase_queue.publish_status(ROBOT_ID, {
                "row": row, "col": col, "heading": heading
            })
        except Exception as fb_err:
            print(f"   ⚠️  Firebase pose sync failed: {fb_err}")

        return jsonify({"ok": True, "pose": state["robot_pose"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── ROUTE 3: Navigate to a room ───────────────────────────────────────────
@app.route("/navigate", methods=["POST"])
def navigate():
    try:
        if state["grid"] is None:
            return jsonify({"error": "No blueprint loaded. Call /upload first."}), 400

        data        = request.json
        target_room = data.get("room", "").strip().lower()

        grid_np = np.array(state["grid"], dtype=np.uint8)

        # ── Room matching: exact first, then partial ──────────────────────
        matched = None
        for room in state["rooms"]:
            if room["name"].lower() == target_room:
                matched = room
                break
        if not matched:
            for room in state["rooms"]:
                if target_room in room["name"].lower() or \
                   room["name"].lower() in target_room:
                    matched = room
                    break

        if not matched:
            available = [r["name"] for r in state["rooms"]]
            return jsonify({
                "error": f"Room '{target_room}' not found",
                "available_rooms": available
            }), 404

        # ── Pixel → grid cell ─────────────────────────────────────────────
        cell_size = state.get("cell_size_px", 10)
        rx, ry    = matched["center"]
        goal_col  = rx // cell_size
        goal_row  = ry // cell_size

        # Snap goal to nearest free cell
        if grid_np[goal_row][goal_col] == 1:
            found = False
            for radius in range(1, 20):
                for dr in range(-radius, radius + 1):
                    for dc in range(-radius, radius + 1):
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

        # Snap start to nearest free cell
        if grid_np[start_row][start_col] == 1:
            found = False
            for radius in range(1, 20):
                for dr in range(-radius, radius + 1):
                    for dc in range(-radius, radius + 1):
                        nr, nc = start_row + dr, start_col + dc
                        if 0 <= nr < grid_np.shape[0] and \
                           0 <= nc < grid_np.shape[1] and \
                           grid_np[nr][nc] == 0:
                            start_row, start_col = nr, nc
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            print(f"   Snapped start to free cell: ({start_row}, {start_col})")

        start = (start_row, start_col)
        goal  = (goal_row, goal_col)

        print(f"🔍 Navigating to {matched['name']}: {start} → {goal}")

        path = astar(grid_np, start, goal)
        if not path:
            return jsonify({"error": "No path found to target room"}), 400

        smoothed = smooth_path(path, grid_np)
        commands = path_to_commands(
            path,
            cell_size_cm=10,
            initial_heading=heading,
            real_cm_per_cell=state["real_cm_per_cell"],
        )

        state["command_queue"] = list(commands)   # popped as ESP32 polls
        state["all_commands"]  = list(commands)   # permanent copy for sequence tracking
        state["current_path"]  = path

        # ── Firebase: publish command queue ───────────────────────────────
        try:
            firebase_queue.publish_queue(ROBOT_ID, commands)
        except Exception as fb_err:
            print(f"   ⚠️  Firebase publish failed: {fb_err}")

        print(f"✅ Path found: {len(path)} cells, {len(commands)} commands")

        return jsonify({
            "target_room": matched["name"],
            "start":       list(start),
            "goal":        list(goal),
            "path_cells":  len(path),
            "path":        [list(p) for p in path],
            "commands":    commands,
        })

    except Exception as e:
        import traceback
        print("❌ ERROR in /navigate:")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ── ROUTE 4: Get current status ───────────────────────────────────────────
@app.route("/status", methods=["GET"])
def status():
    return jsonify({
        "parse_status":     state["parse_status"],
        "robot_pose":       state["robot_pose"],
        "rooms_detected":   len(state["rooms"]),
        "command_queue":    state["command_queue"],
        "grid_loaded":      state["grid"] is not None,
        "real_cm_per_cell": state["real_cm_per_cell"],
        "robot_params":     state["robot_params"],
        "firebase_enabled": firebase_queue.enabled,
    })


# ── ROUTE 5: ESP32 polls for next command ─────────────────────────────────
@app.route("/get-command", methods=["GET"])
def get_command():
    if state["command_queue"]:
        cmd = state["command_queue"].pop(0)
        # Compute 1-based sequence number
        seq = len(state["all_commands"]) - len(state["command_queue"])
        return jsonify({
            "command":   cmd,
            "sequence":  seq,
            "remaining": len(state["command_queue"]),
        })
    return jsonify({"command": "DONE", "sequence": 0, "remaining": 0})


# ── ROUTE 6: ESP32 calls this after completing a command ──────────────────
@app.route("/command-done", methods=["POST"])
def command_done():
    """
    ESP32 POSTs {"sequence": N} when it finishes executing command N.
    Marks that command DONE in Firebase and advances active_sequence.
    In-memory queue is already popped by /get-command, so nothing to do there.
    """
    data = request.get_json(silent=True) or {}
    seq  = data.get("sequence")

    if firebase_queue.enabled and seq is not None:
        try:
            firebase_queue._db.reference(
                f"/robots/{ROBOT_ID}/queue/{seq}/status"
            ).set("DONE")
            # Advance active_sequence pointer
            firebase_queue._db.reference(
                f"/robots/{ROBOT_ID}/meta/active_sequence"
            ).set(seq + 1)
            print(f"   ✅ Command {seq} marked DONE in Firebase")
        except Exception as fb_err:
            print(f"   ⚠️  Firebase command-done failed: {fb_err}")

    return jsonify({"ok": True, "sequence": seq})


# ── MAIN ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 Flask server starting on http://localhost:8080")
    print(f"   Firebase: {'✅ enabled' if firebase_queue.enabled else '⚠️  disabled — set FIREBASE_CREDENTIALS and FIREBASE_DATABASE_URL'}")
    print("Routes:")
    print("  POST /upload        — upload blueprint image")
    print("  POST /set-pose      — set robot start position")
    print("  POST /navigate      — navigate to a room")
    print("  GET  /status        — get system status")
    print("  GET  /get-command   — ESP32 polls for next command")
    print("  POST /command-done  — ESP32 confirms command executed")
    app.run(debug=True, port=8080)