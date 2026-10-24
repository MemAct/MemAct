"use strict";
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="memact-token"]').content;
let state = {mode: "idle", recordings: []};
let recording = null;
let currentId = null;
let selected = new Set();
let focusedStep = null;
let revision = -1;
let confirmToken = null;
let polling = false;
let busy = false;
let menuRecordingId = null;
let menuTrigger = null;
let recordingAction = null;
let actionRecordingId = null;
let stepDeletion = null;
let exportRequest = null;

function setHistoryCollapsed(collapsed) {
  document.querySelector(".workspace").classList.toggle("history-collapsed", collapsed);
  $("recording-list").hidden = collapsed;
  $("toggle-history").setAttribute("aria-expanded", String(!collapsed));
  const label = collapsed ? "Show recording history" : "Hide recording history";
  $("toggle-history").setAttribute("aria-label", label);
  $("toggle-history").title = label;
  $("toggle-history").querySelector("path").setAttribute("d", collapsed ? "m10 6 6 6-6 6" : "m14 6-6 6 6 6");
  closeRecordingMenu();
}
$("toggle-history").addEventListener("click", () => {
  const collapsed = !document.querySelector(".workspace").classList.contains("history-collapsed");
  setHistoryCollapsed(collapsed);
  try {localStorage.setItem("memact-history-collapsed", String(collapsed));} catch {}
});
function expandHistory(focusSearch = false) {
  setHistoryCollapsed(false);
  try {localStorage.setItem("memact-history-collapsed", "false");} catch {}
  if (focusSearch) $("library-search").focus();
  else $("recording-list").querySelector(".recording-link")?.focus();
}
$("rail-search").addEventListener("click", () => expandHistory(true));
$("rail-history").addEventListener("click", () => expandHistory());
try {setHistoryCollapsed(localStorage.getItem("memact-history-collapsed") === "true");} catch {}
// Let the scrollbar use the sidebar's outer inset, keeping cards aligned with the fields.
new ResizeObserver(() => {
  const list = $("recording-list");
  if (!list.hidden) list.style.setProperty("--history-gutter", `${list.offsetWidth - list.clientWidth}px`);
}).observe($("recording-list"));

async function api(path, data) {
  const response = await fetch(path, {method: data === undefined ? "GET" : "POST",
    headers: {"X-MemAct-Token": token, ...(data === undefined ? {} : {"Content-Type": "application/json"})},
    ...(data === undefined ? {} : {body: JSON.stringify(data)})});
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(result.error || `Request failed (${response.status})`);
  }
  return response;
}

function message(text) {$("message").textContent = text;}
function stepLabel(step) {return step.label.custom || step.label.template;}
function idle() {return ["idle", "error"].includes(state.mode) && !busy;}
function shownSteps() {
  const query = $("step-search").value.toLocaleLowerCase().trim();
  return (recording?.steps || []).filter(step => !query || [stepLabel(step), step.nearby_text,
    step.page.title, step.page.url, step.target?.role, step.target?.name].join(" ").toLocaleLowerCase().includes(query));
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function closeRecordingMenu(restoreFocus = false) {
  $("recording-menu").hidden = true;
  menuTrigger?.setAttribute("aria-expanded", "false");
  if (restoreFocus && menuTrigger?.isConnected) menuTrigger.focus();
}

function openRecordingMenu(id, trigger, x, y) {
  closeRecordingMenu();
  menuRecordingId = id; menuTrigger = trigger;
  trigger.setAttribute("aria-expanded", "true");
  const menu = $("recording-menu");
  for (const button of menu.querySelectorAll("button")) button.disabled = !idle();
  menu.hidden = false;
  const rect = trigger.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(x ?? rect.left, innerWidth - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(y ?? rect.bottom + 4, innerHeight - menu.offsetHeight - 8))}px`;
  menu.querySelector("button:not(:disabled)")?.focus();
}

$("recording-options").addEventListener("click", event => openRecordingMenu(currentId, event.currentTarget));
document.addEventListener("pointerdown", event => {
  if (!event.target.closest("#recording-menu, .more-button")) closeRecordingMenu();
});
window.addEventListener("resize", () => closeRecordingMenu());
document.addEventListener("scroll", () => closeRecordingMenu(), true);
$("recording-menu").addEventListener("keydown", event => {
  if (event.key === "Escape") {event.preventDefault(); closeRecordingMenu(true);}
  if (event.key === "Tab") closeRecordingMenu();
  if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    const items = [...$("recording-menu").querySelectorAll("button:not(:disabled)")];
    const index = items.indexOf(document.activeElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 :
      (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  }
});
$("recording-menu").addEventListener("click", event => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  const item = state.recordings.find(item => item.id === menuRecordingId);
  if (!action || !item || !idle()) return;
  actionRecordingId = item.id; recordingAction = action;
  closeRecordingMenu();
  const rename = action === "rename";
  $("recording-action-heading").textContent = rename ? "Rename recording" : "Delete recording?";
  $("rename-label").hidden = !rename;
  $("rename-recording").hidden = !rename;
  $("rename-recording").disabled = !rename;
  $("rename-recording").value = item.title;
  $("delete-recording-copy").hidden = rename;
  $("delete-recording-copy").textContent = `“${item.title}” and all its steps will be permanently deleted.`;
  $("save-recording-action").textContent = rename ? "Save" : "Delete recording";
  $("save-recording-action").className = rename ? "primary" : "danger";
  $("recording-action-error").hidden = true;
  $("recording-action-dialog").showModal();
  if (rename) {$("rename-recording").focus(); $("rename-recording").select();}
  else $("cancel-recording-action").focus();
});
$("cancel-recording-action").addEventListener("click", () => $("recording-action-dialog").close());
$("recording-action-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = $("save-recording-action"); button.disabled = true;
  try {
    if (recordingAction === "rename") {
      await api(`/api/recordings/${actionRecordingId}/edit`, {title: $("rename-recording").value});
    } else {
      await api(`/api/recordings/${actionRecordingId}/remove`, {});
    }
    $("recording-action-dialog").close(); await refresh(true);
  } catch (error) {
    $("recording-action-error").textContent = error.message; $("recording-action-error").hidden = false;
  } finally {button.disabled = false;}
});

function renderLibrary() {
  const query = $("library-search").value.toLocaleLowerCase().trim();
  $("recording-list").replaceChildren();
  for (const item of state.recordings.filter(item => !query || `${item.title} ${item.start_url}`.toLocaleLowerCase().includes(query))) {
    const row = element("div", "recording-row");
    row.dataset.id = item.id;
    const button = element("button", "recording-link");
    button.setAttribute("aria-current", String(item.id === currentId));
    button.append(element("span", "name", item.title));
    const date = new Date(item.created_at).toLocaleDateString(undefined, {month: "short", day: "numeric"});
    button.append(element("span", "meta", `${item.step_count} ${item.step_count === 1 ? "step" : "steps"} · ${date}`));
    button.addEventListener("click", () => run(() => loadRecording(item.id)));
    const more = element("button", "quiet more-button history-more");
    more.innerHTML = $("recording-options").innerHTML;
    more.setAttribute("aria-label", `Options for ${item.title}`);
    more.setAttribute("aria-haspopup", "menu");
    more.setAttribute("aria-expanded", "false");
    more.addEventListener("click", () => openRecordingMenu(item.id, more));
    row.addEventListener("contextmenu", event => {
      event.preventDefault(); openRecordingMenu(item.id, more, event.clientX, event.clientY);
    });
    button.addEventListener("keydown", event => {
      if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
        event.preventDefault(); openRecordingMenu(item.id, more);
      }
    });
    row.append(button, more);
    $("recording-list").append(row);
  }
  $("library-empty").hidden = state.recordings.length > 0;
}

async function loadRecording(id, preserve = false) {
  const response = await api(`/api/recordings/${id}`);
  recording = await response.json();
  currentId = id;
  if (!preserve) {selected.clear(); focusedStep = null; $("step-search").value = "";}
  selected = new Set([...selected].filter(id => recording.steps.some(step => step.id === id)));
  $("empty").hidden = true;
  $("editor").hidden = false;
  if (document.activeElement !== $("recording-name")) $("recording-name").value = recording.title;
  $("recording-url").textContent = recording.start_url;
  $("step-count").textContent = `${recording.steps.length} ${recording.steps.length === 1 ? "step" : "steps"}`;
  renderLibrary(); renderTimeline(); renderDetails(); updateControls();
}

function renderTimeline() {
  const activeInput = document.activeElement;
  if (activeInput?.classList.contains("step-label")) return;
  const fragment = document.createDocumentFragment();
  for (const step of shownSteps()) {
    const index = recording.steps.indexOf(step) + 1;
    const row = element("li", "step" + (focusedStep === step.id ? " active" : ""));
    row.dataset.id = step.id;
    const checkbox = element("input");
    checkbox.type = "checkbox"; checkbox.checked = selected.has(step.id);
    checkbox.setAttribute("aria-label", `Select step ${index}`);
    checkbox.addEventListener("click", event => event.stopPropagation());
    checkbox.addEventListener("change", () => {
      checkbox.checked ? selected.add(step.id) : selected.delete(step.id);
      focusedStep = step.id; renderDetails(); updateControls();
    });
    const content = element("div", "step-content");
    const input = element("input", "step-label");
    input.value = stepLabel(step); input.maxLength = 2000;
    input.setAttribute("aria-label", `Label for step ${index}`);
    input.disabled = !idle();
    input.addEventListener("focus", () => {focusedStep = step.id; renderDetails();});
    input.addEventListener("click", event => event.stopPropagation());
    input.addEventListener("keydown", event => {if (event.key === "Enter") input.blur();});
    input.addEventListener("change", () => run(async () => {
      await api(`/api/recordings/${currentId}/edit`, {step_id: step.id, label: input.value});
      await loadRecording(currentId, true);
    }));
    const location = step.frame.length ? `Frame · ${step.page.title || step.page.id}` : step.page.title || step.page.id;
    content.append(input, element("div", "step-meta", `${step.action.type.replaceAll("_", " ")} · ${location}`));
    row.append(checkbox, element("span", "step-index", String(index)), content);
    if (step.screenshot?.data_url) {
      const image = element("img", "step-thumb");
      image.src = step.screenshot.data_url; image.alt = ""; image.loading = "lazy";
      row.append(image);
    } else row.append(element("span", "step-no-thumb", step.screenshot?.status === "pending" ? "Capturing" : "No image"));
    row.addEventListener("click", () => {
      focusedStep = step.id;
      selected.has(step.id) ? selected.delete(step.id) : selected.add(step.id);
      renderTimeline(); renderDetails(); updateControls();
    });
    fragment.append(row);
  }
  $("timeline").replaceChildren(fragment);
  $("no-matches").hidden = shownSteps().length > 0;
}

function fields(container, entries) {
  container.replaceChildren();
  for (const [key, value] of entries) {
    if (value === undefined || value === null || value === "") continue;
    container.append(element("dt", "", key), element("dd", "", String(value)));
  }
}

function renderDetails() {
  const step = recording?.steps.find(step => step.id === focusedStep);
  $("inspector-empty").hidden = !!step;
  $("step-details").hidden = !step;
  if (!step) return;
  const index = recording.steps.indexOf(step) + 1;
  $("detail-heading").textContent = `Step ${index}`;
  $("image-open").hidden = !step.screenshot?.data_url;
  $("image-missing").hidden = !!step.screenshot?.data_url;
  if (step.screenshot?.data_url) $("detail-image").src = step.screenshot.data_url;
  $("screenshot-caption").textContent = step.screenshot?.data_url ? "Captured after the action. Sensitive fields hidden." : "";
  fields($("detail-fields"), [["Action", step.action.type.replaceAll("_", " ")], ["Role", step.target?.role],
    ["Name", step.target?.name], ["Page", step.page.title], ["URL", step.page.url],
    ["Frame", step.frame.length ? step.frame.map(frame => frame.frame_name || frame.selector).join(" / ") : null],
    ["Target", step.target?.selector], ["Image URL", step.screenshot?.url && step.screenshot.url !== step.page.url ? step.screenshot.url : null]]);
  $("detail-context").textContent = step.nearby_text || "";
  $("reset-label").hidden = !step.label.custom;
  $("reset-label").disabled = !idle();
}

function updateControls() {
  const isIdle = idle();
  const isRecording = state.mode === "recording";
  $("record-button").hidden = isRecording;
  $("stop-button").hidden = !isRecording;
  $("record-button").disabled = !isIdle;
  $("new-recording-button").disabled = !isIdle;
  $("import-button").disabled = !isIdle;
  $("recording-name").disabled = !isIdle;
  if (!isIdle) closeRecordingMenu();
  $("replay-button").disabled = !isIdle || !selected.size;
  $("delete-button").disabled = !isIdle || !selected.size;
  $("rerecord-button").disabled = !isIdle || selected.size !== 1;
  $("export-button").disabled = !selected.size;
  $("selection-count").textContent = selected.size ? `${selected.size} selected` : "Select all";
  const visible = shownSteps();
  $("select-all").checked = visible.length > 0 && visible.every(step => selected.has(step.id));
  $("select-all").indeterminate = visible.some(step => selected.has(step.id)) && !$("select-all").checked;
  const replacing = ["preparing", "rerecording"].includes(state.mode);
  $("preparation").hidden = !replacing;
  $("preparation-copy").textContent = state.mode === "rerecording" ? "Perform the replacement action in the browser." :
    "Restore the page to the state before this step, then record the replacement action.";
  $("arm-button").hidden = state.mode === "rerecording";
  $("cancel-replay").hidden = !["replaying", "confirm"].includes(state.mode);
}

function renderConfirmation() {
  const pending = state.pending;
  if (!pending) {
    if ($("confirm-dialog").open) $("confirm-dialog").close();
    confirmToken = null;
    return;
  }
  if (pending.token === confirmToken) return;
  confirmToken = pending.token;
  $("confirm-label").textContent = pending.label;
  fields($("confirm-fields"), [["Action", pending.action.type.replaceAll("_", " ")], ["Element", pending.target ? `${pending.target.role || pending.target.tag} “${pending.target.name || pending.target.text || ""}”` : null],
    ["Page", pending.action.type === "navigate" ? pending.action.url : pending.page.url],
    ["Value", pending.action.type === "fill" ? pending.action.value : null],
    ["Key", pending.action.key]]);
  $("confirm-reason").textContent = pending.reason;
  $("file-reselection").hidden = !pending.needs_files;
  $("expected-files").textContent = (pending.action.files || []).map(file => `${file.name} (${file.size.toLocaleString()} bytes)`).join(", ");
  $("replay-files").value = "";
  $("confirm-error").hidden = true;
  $("confirm-continue").disabled = false;
  if (!$("confirm-dialog").open) $("confirm-dialog").showModal();
}

async function refresh(force = false) {
  if (polling) return;
  polling = true;
  try {
    const next = await (await api("/api/state")).json();
    const modeChanged = state.mode !== next.mode;
    const changed = next.revision !== revision;
    state = next;
    if (currentId && !state.recordings.some(item => item.id === currentId)) {
      currentId = null; recording = null; selected.clear(); focusedStep = null;
      $("editor").hidden = true; $("empty").hidden = false;
    }
    if (!currentId && state.recordings.length) currentId = state.recordings[0].id;
    if (["recording", "rerecording", "preparing"].includes(state.mode) && state.recording_id !== currentId) {
      currentId = state.recording_id; selected.clear(); focusedStep = null;
    }
    if (currentId && (force || changed)) await loadRecording(currentId, true);
    else if (changed) renderLibrary();
    if (modeChanged && recording) renderTimeline();
    revision = next.revision;
    updateControls(); renderConfirmation();
    message(next.load_errors.length ? next.load_errors[0] : next.message || "");
  } catch (error) {message(error.message);}
  finally {polling = false;}
}

async function run(operation) {
  try {await operation(); await refresh(true);}
  catch (error) {message(error.message);}
}

function showRecord() {
  $("record-error").hidden = true;
  $("record-error").textContent = "";
  $("record-dialog").showModal();
  $("new-url").focus();
}
$("record-button").addEventListener("click", showRecord);
$("new-recording-button").addEventListener("click", showRecord);
$("first-record").addEventListener("click", showRecord);
document.querySelector(".close-record").addEventListener("click", () => $("record-dialog").close());
$("use-mock").addEventListener("click", () => {$("new-url").value = location.origin + "/mock"; $("new-title").value = "Local test";});
$("new-url").addEventListener("input", () => {$("record-error").hidden = true;});
$("record-form").addEventListener("submit", async event => {
  event.preventDefault();
  $("start-button").disabled = true; $("start-button").textContent = "Opening browser…";
  try {
    if (!$("new-url").value.trim()) throw new Error("Enter a web address, such as google.com");
    const result = await (await api("/api/record/start", {url: $("new-url").value, title: $("new-title").value || "Untitled recording"})).json();
    currentId = result.id; selected.clear(); focusedStep = null;
    $("record-dialog").close(); await refresh(true);
  } catch (error) {$("record-error").textContent = error.message; $("record-error").hidden = false;}
  finally {$("start-button").disabled = false; $("start-button").textContent = "Start recording";}
});
$("stop-button").addEventListener("click", () => run(async () => {$("stop-button").disabled = true; try {await api("/api/record/stop", {});} finally {$("stop-button").disabled = false;}}));
$("library-search").addEventListener("input", renderLibrary);
$("step-search").addEventListener("input", () => {renderTimeline(); updateControls();});
$("select-all").addEventListener("change", event => {
  for (const step of shownSteps()) event.target.checked ? selected.add(step.id) : selected.delete(step.id);
  renderTimeline(); updateControls();
});
$("recording-name").addEventListener("change", () => run(async () => {await api(`/api/recordings/${currentId}/edit`, {title: $("recording-name").value});}));
$("delete-button").addEventListener("click", () => {
  if (!idle() || !selected.size) return;
  stepDeletion = {recordingId: currentId, ids: [...selected]};
  const noun = selected.size === 1 ? "step" : "steps";
  $("delete-steps-heading").textContent = `Delete ${selected.size} selected ${noun}?`;
  $("delete-steps-copy").textContent = `This will permanently delete ${selected.size === 1 ? "this step" : "these steps"} from “${recording.title}”.`;
  $("confirm-delete-steps").textContent = selected.size === 1 ? "Delete step" : "Delete steps";
  $("delete-steps-error").hidden = true;
  $("delete-steps-dialog").showModal();
  $("cancel-delete-steps").focus();
});
$("cancel-delete-steps").addEventListener("click", () => $("delete-steps-dialog").close());
$("delete-steps-dialog").addEventListener("close", () => {stepDeletion = null;});
$("delete-steps-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!stepDeletion) return;
  const deletion = stepDeletion;
  $("confirm-delete-steps").disabled = true;
  try {
    await api(`/api/recordings/${deletion.recordingId}/delete`, {ids: deletion.ids});
    if (currentId === deletion.recordingId) {
      for (const id of deletion.ids) selected.delete(id);
      if (deletion.ids.includes(focusedStep)) focusedStep = null;
    }
    $("delete-steps-dialog").close(); await refresh(true);
  } catch (error) {
    $("delete-steps-error").textContent = error.message; $("delete-steps-error").hidden = false;
  } finally {$("confirm-delete-steps").disabled = false;}
});
$("replay-button").addEventListener("click", () => run(async () => {await api("/api/replay/start", {recording_id: currentId, ids: [...selected]});}));
$("rerecord-button").addEventListener("click", () => run(async () => {await api("/api/rerecord/prepare", {recording_id: currentId, step_id: [...selected][0]});}));
$("arm-button").addEventListener("click", () => run(async () => {await api("/api/rerecord/arm", {});}));
$("cancel-replacement").addEventListener("click", () => run(async () => {await api("/api/record/stop", {});}));
$("reset-label").addEventListener("click", () => run(async () => {await api(`/api/recordings/${currentId}/edit`, {step_id: focusedStep, label: null});}));
$("close-detail").addEventListener("click", () => {focusedStep = null; renderTimeline(); renderDetails();});
$("image-open").addEventListener("click", () => {$("full-image").src = $("detail-image").src; $("image-dialog").showModal();});
$("close-image").addEventListener("click", () => $("image-dialog").close());

async function exportSelection(format) {
  if (!exportRequest) return;
  const request = exportRequest;
  $("export-text").disabled = true; $("export-json").disabled = true;
  try {
    const response = await api(`/api/recordings/${request.recordingId}/export`, {ids: request.ids, format});
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = element("a");
    anchor.href = url;
    anchor.download = (request.title.replace(/[^\p{L}\p{N}_-]+/gu, "-").slice(0, 80) || "MemAct") + (format === "text" ? ".txt" : ".json");
    anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    $("export-dialog").close();
  } catch (error) {
    $("export-error").textContent = error.message; $("export-error").hidden = false;
  } finally {$("export-text").disabled = false; $("export-json").disabled = false;}
}
$("export-button").addEventListener("click", () => {
  if (!selected.size || !recording) return;
  exportRequest = {recordingId: currentId, title: recording.title, ids: [...selected]};
  $("export-heading").textContent = `Export ${selected.size} selected ${selected.size === 1 ? "step" : "steps"} as`;
  $("export-error").hidden = true;
  $("export-dialog").showModal();
});
$("cancel-export").addEventListener("click", () => $("export-dialog").close());
$("export-dialog").addEventListener("close", () => {exportRequest = null;});
$("export-text").addEventListener("click", () => exportSelection("text"));
$("export-json").addEventListener("click", () => exportSelection("json"));
$("import-button").addEventListener("click", () => $("import-file").click());
$("import-file").addEventListener("change", () => run(async () => {
  const file = $("import-file").files[0];
  if (!file) return;
  if (file.size > 100 * 1024 * 1024) throw new Error("Import files must be under 100 MB");
  const result = await (await api("/api/import", JSON.parse(await file.text()))).json();
  await loadRecording(result.id); $("import-file").value = "";
}));

function filePayload(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({name: file.name, mime: file.type || "application/octet-stream", data: reader.result.split(",")[1]});
    reader.onerror = () => reject(new Error("Could not read the selected file"));
    reader.readAsDataURL(file);
  });
}
$("confirm-continue").addEventListener("click", async () => {
  $("confirm-continue").disabled = true;
  try {
    const files = Array.from($("replay-files").files);
    if (files.reduce((sum, file) => sum + file.size, 0) > 25 * 1024 * 1024) throw new Error("Select up to 25 MB per replay step");
    const data = await Promise.all(files.map(filePayload));
    await api("/api/replay/confirm", {token: confirmToken, files: data});
    $("confirm-dialog").close(); await refresh();
  } catch (error) {$("confirm-error").textContent = error.message; $("confirm-error").hidden = false; $("confirm-continue").disabled = false;}
});
const cancelReplay = () => run(async () => {await api("/api/replay/cancel", {});});
$("confirm-cancel").addEventListener("click", cancelReplay);
$("cancel-replay").addEventListener("click", cancelReplay);
$("confirm-dialog").addEventListener("cancel", event => {event.preventDefault(); cancelReplay();});
refresh(true);
setInterval(() => refresh(), 850);
