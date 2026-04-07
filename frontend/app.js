const state = {
  session: null,
  rooms: [],
  imageNames: {},
};

function parseSummary(session) {
  const result = session.parse_result;
  const roomNames = result.rooms.map((room) => room.name).join(", ") || "none";
  const warnings = result.missing_fields.length ? result.missing_fields.join(", ") : "none";
  return {
    session_id: session.session_id,
    robot_id: session.robot_id,
    parse_state: result.parse_state,
    unit: result.unit,
    orientation: result.orientation?.direction || null,
    rooms_detected: result.rooms.length,
    room_names: roomNames,
    grid_rows: result.grid_rows,
    grid_cols: result.grid_cols,
    image_names: state.imageNames,
    missing_fields: warnings,
  };
}

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }
  return response.json();
}

async function refreshHealth() {
  try {
    const health = await api("/health");
    document.getElementById("health-status").textContent = health.status;
    document.getElementById("firebase-status").textContent = health.firebase_enabled ? "Enabled" : `Disabled${health.firebase_error ? `: ${health.firebase_error}` : ""}`;
  } catch (error) {
    document.getElementById("health-status").textContent = error.message;
  }
}

function renderRooms() {
  const root = document.getElementById("room-buttons");
  root.innerHTML = "";
  state.rooms.forEach((room) => {
    const button = document.createElement("button");
    button.textContent = room.name;
    button.addEventListener("click", () => navigateToRoom(room.name));
    root.appendChild(button);
  });
}

function renderQueue(queue = []) {
  const root = document.getElementById("queue-list");
  root.innerHTML = "";
  queue.forEach((item) => {
    const div = document.createElement("div");
    div.className = "queue-item";
    div.textContent = `#${item.sequence} ${item.command} (${item.status})`;
    root.appendChild(div);
  });
}

function setPreviewImage(kind) {
  if (!state.session) {
    return;
  }
  const imageName = state.imageNames[kind];
  if (!imageName) {
    document.getElementById("image-status").textContent = "Preview image not available.";
    return;
  }
  const image = document.getElementById("blueprint-image");
  document.getElementById("image-status").textContent = `Loading ${kind} preview...`;
  image.src = `/api/sessions/${state.session.session_id}/image/${imageName}?t=${Date.now()}`;
}

async function navigateToRoom(room) {
  if (!state.session) {
    return;
  }
  const response = await api("/navigate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: state.session.session_id, room }),
  });
  document.getElementById("directions-output").textContent = response.directions.join("\n");
  renderQueue(response.queue);
  if (response.overlay_image_name) {
    document.getElementById("blueprint-image").src = `/api/sessions/${state.session.session_id}/image/${response.overlay_image_name}`;
  }
}

async function sendManualCommands(commands) {
  if (!state.session) {
    return;
  }
  const session = await api("/manual", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: state.session.session_id, commands }),
  });
  renderQueue(session.queue);
}

function parseVoiceIntent(input) {
  const text = input.trim().toLowerCase();
  if (!text) {
    return null;
  }
  if (text.startsWith("take me to ") || text.startsWith("go to ")) {
    return { type: "room", value: text.replace(/^take me to |^go to /, "") };
  }
  if (text.includes("move forward")) {
    const amount = text.match(/(\d+)/)?.[1] || "10";
    return { type: "manual", value: [`F${amount}cm`] };
  }
  if (text.includes("move backward")) {
    const amount = text.match(/(\d+)/)?.[1] || "5";
    return { type: "manual", value: [`B${amount}cm`] };
  }
  if (text.includes("turn left")) {
    const amount = text.match(/(\d+)/)?.[1] || "90";
    return { type: "manual", value: [`L${amount}`] };
  }
  if (text.includes("turn right")) {
    const amount = text.match(/(\d+)/)?.[1] || "90";
    return { type: "manual", value: [`R${amount}`] };
  }
  if (text.includes("stop") || text.includes("cancel")) {
    return { type: "manual", value: ["STOP"] };
  }
  return null;
}

document.getElementById("parse-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const response = await fetch("/api/blueprints/parse", { method: "POST", body: form });
  const data = await response.json();
  state.session = data.session;
  state.rooms = data.session.parse_result.rooms;
  state.imageNames = {
    original: data.session.parse_result.original_image_name,
    raw: data.session.parse_result.raw_grid_image_name,
    inflated: data.session.parse_result.inflated_grid_image_name,
  };
  document.getElementById("parse-output").textContent = JSON.stringify(parseSummary(data.session), null, 2);
  renderRooms();
  renderQueue(data.session.queue);
  setPreviewImage(state.imageNames.raw ? "raw" : "original");
});

document.getElementById("blueprint-image").addEventListener("click", (event) => {
  const rect = event.target.getBoundingClientRect();
  document.getElementById("pose-x").value = Math.round(event.clientX - rect.left);
  document.getElementById("pose-y").value = Math.round(event.clientY - rect.top);
});

document.getElementById("pose-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.session) {
    return;
  }
  const params = new URLSearchParams({
    session_id: state.session.session_id,
    x: document.getElementById("pose-x").value,
    y: document.getElementById("pose-y").value,
    heading: document.getElementById("pose-heading").value,
  });
  const session = await api(`/pose?${params.toString()}`, { method: "POST" });
  state.session = session;
  document.getElementById("parse-output").textContent = JSON.stringify(parseSummary(session), null, 2);
});

document.querySelectorAll(".manual-btn").forEach((button) => {
  button.addEventListener("click", () => sendManualCommands([button.dataset.command]));
});

document.getElementById("voice-run").addEventListener("click", async () => {
  const intent = parseVoiceIntent(document.getElementById("voice-input").value);
  if (!intent) {
    alert("Could not parse voice command.");
    return;
  }
  if (intent.type === "room") {
    await navigateToRoom(intent.value);
  } else {
    await sendManualCommands(intent.value);
  }
});

document.getElementById("voice-listen").addEventListener("click", () => {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    alert("Speech recognition is not available in this browser.");
    return;
  }
  const recognition = new SpeechRecognition();
  recognition.onresult = (event) => {
    document.getElementById("voice-input").value = event.results[0][0].transcript;
  };
  recognition.start();
});

document.getElementById("show-original").addEventListener("click", () => setPreviewImage("original"));
document.getElementById("show-grid-raw").addEventListener("click", () => setPreviewImage("raw"));
document.getElementById("show-grid-inflated").addEventListener("click", () => setPreviewImage("inflated"));

document.getElementById("blueprint-image").addEventListener("load", () => {
  document.getElementById("image-status").textContent = "Preview loaded.";
});

document.getElementById("blueprint-image").addEventListener("error", () => {
  document.getElementById("image-status").textContent = "Preview failed to load. Try a hard refresh.";
});

refreshHealth();
setInterval(async () => {
  if (!state.session) {
    return;
  }
  try {
    const session = await api(`/sessions/${state.session.session_id}`);
    state.session = session;
    renderQueue(session.queue);
  } catch (error) {
    console.error(error);
  }
}, 4000);
