const state = {
  session: null,
  rooms: [],
  imageNames: {},
};

function setProcessingStatus(message) {
  document.getElementById("processing-status").textContent = message;
}

function setImageLoading(loading, message = "Processing image...") {
  const loader = document.getElementById("image-loader");
  const text = document.getElementById("image-loader-text");
  text.textContent = message;
  loader.classList.toggle("is-hidden", !loading);
}

function setControlsDisabled(disabled) {
  document.querySelectorAll("button, input, select").forEach((element) => {
    if (element.id === "voice-listen") {
      return;
    }
    element.disabled = disabled;
  });
}

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
  setImageLoading(true, `Loading ${kind} preview...`);
  document.getElementById("image-status").textContent = `Loading ${kind} preview...`;
  image.src = `/api/sessions/${state.session.session_id}/image/${imageName}?t=${Date.now()}`;
}

async function navigateToRoom(room) {
  if (!state.session) {
    return;
  }
  try {
    setControlsDisabled(true);
    setProcessingStatus(`Planning route to ${room}...`);
    setImageLoading(true, `Planning route to ${room}...`);
    const response = await api("/navigate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.session.session_id, room }),
    });
    document.getElementById("directions-output").textContent = response.directions.join("\n");
    renderQueue(response.queue);
    if (response.overlay_image_name) {
      document.getElementById("image-status").textContent = "Loading traced route...";
      document.getElementById("blueprint-image").src = `/api/sessions/${state.session.session_id}/image/${response.overlay_image_name}?t=${Date.now()}`;
    }
    setProcessingStatus(`Route ready for ${room}.`);
  } catch (error) {
    setProcessingStatus(`Route planning failed: ${error.message}`);
    document.getElementById("directions-output").textContent = `Route planning failed.\n${error.message}`;
    setImageLoading(false);
  } finally {
    setControlsDisabled(false);
  }
}

async function sendManualCommands(commands) {
  if (!state.session) {
    return;
  }
  try {
    setControlsDisabled(true);
    setProcessingStatus("Sending manual command...");
    const session = await api("/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.session.session_id, commands }),
    });
    renderQueue(session.queue);
    setProcessingStatus("Manual command queued.");
  } finally {
    setControlsDisabled(false);
  }
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
  try {
    setControlsDisabled(true);
    setProcessingStatus("Uploading blueprint and processing images...");
    setImageLoading(true, "Processing blueprint image...");
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
    setProcessingStatus("Blueprint processed successfully.");
  } catch (error) {
    setProcessingStatus(`Processing failed: ${error.message}`);
    document.getElementById("parse-output").textContent = `Processing failed.\n${error.message}`;
    setImageLoading(false);
  } finally {
    setControlsDisabled(false);
  }
});

document.getElementById("blueprint-image").addEventListener("click", (event) => {
  const image = event.target;
  if (!image.naturalWidth || !image.naturalHeight) {
    return;
  }
  const rect = image.getBoundingClientRect();
  const scaleX = image.naturalWidth / rect.width;
  const scaleY = image.naturalHeight / rect.height;
  const x = Math.round((event.clientX - rect.left) * scaleX);
  const y = Math.round((event.clientY - rect.top) * scaleY);
  document.getElementById("pose-x").value = x;
  document.getElementById("pose-y").value = y;
  setProcessingStatus(`Pose selected at x=${x}, y=${y}.`);
});

document.getElementById("pose-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.session) {
    return;
  }
  try {
    setControlsDisabled(true);
    const params = new URLSearchParams({
      session_id: state.session.session_id,
      x: document.getElementById("pose-x").value,
      y: document.getElementById("pose-y").value,
      heading: document.getElementById("pose-heading").value,
    });
    const session = await api(`/pose?${params.toString()}`, { method: "POST" });
    state.session = session;
    document.getElementById("parse-output").textContent = JSON.stringify(parseSummary(session), null, 2);
    setProcessingStatus("Robot pose updated.");
  } finally {
    setControlsDisabled(false);
  }
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
  setImageLoading(false);
  document.getElementById("image-status").textContent = "Preview loaded.";
});

document.getElementById("blueprint-image").addEventListener("error", () => {
  setImageLoading(false);
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
