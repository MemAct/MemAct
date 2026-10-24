"""The portable JSON contract. Labels never drive automation."""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

VERSION = 1
ACTIONS = {"navigate", "click", "fill", "press", "check", "select", "upload", "drop_files", "drag", "open_tab", "close_tab"}
MAX_STEPS = 2000
MAX_TEXT = 20000


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def uid():
    return uuid4().hex


def validate_url(url):
    if not isinstance(url, str) or len(url) > 8192:
        raise ValueError("Invalid URL")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use an http:// or https:// URL without embedded credentials")
    if re.search(r"\s", parsed.netloc):
        raise ValueError("The web address contains an invalid hostname")
    try:
        parsed.port
    except ValueError:
        raise ValueError("The web address contains an invalid port") from None
    return url


def normalize_start_url(value):
    """Accept address-bar input, while keeping imported/replayed URLs strict."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Enter a web address, such as google.com")
    value = value.strip()
    if value.startswith("//"):
        return validate_url("https:" + value)
    has_scheme = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value)
    host_and_port = re.match(r"^(?:[a-zA-Z0-9.-]+|\[[0-9a-fA-F:]+\]):[0-9]+(?:[/?#]|$)", value)
    if has_scheme and not host_and_port:
        return validate_url(value)
    host = urlsplit("//" + value).hostname or ""
    scheme = "http" if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost") else "https"
    return validate_url(f"{scheme}://{value}")


def new_recording(url, title="Untitled recording"):
    return {"version": VERSION, "id": uid(), "title": title[:200], "created_at": now(),
            "updated_at": now(), "start_url": validate_url(url), "steps": []}


def template(step):
    action = step["action"]
    kind = action["type"]
    target = step.get("target") or {}
    name = target.get("name") or target.get("text") or target.get("tag") or "element"
    what = f'{target.get("role") or "element"} “{name}”'
    if kind == "navigate":
        return ("Reached " if action.get("mode") == "observe" else "Opened ") + action["url"]
    if kind == "open_tab":
        return "Opened a new tab"
    if kind == "close_tab":
        return "Closed tab"
    if kind == "fill":
        return f'Entered “{action.get("value", "")}” in {what}'
    if kind == "press":
        return f'Pressed {action["key"]} on {what}'
    if kind == "check":
        return f'{"Checked" if action["checked"] else "Unchecked"} {what}'
    if kind == "select":
        return f'Selected “{", ".join(action["values"])}” in {what}'
    if kind in {"upload", "drop_files"}:
        names = ", ".join(f["name"] for f in action.get("files", [])) or "no files"
        return f'{"Uploaded" if kind == "upload" else "Dropped"} {names}'
    if kind == "drag":
        destination = action.get("destination") or {}
        return f'Dragged {what} to “{destination.get("name") or destination.get("tag") or "destination"}”'
    return f'Clicked {what}'


def safety(step):
    """Only passive observations bypass confirmation. Page handlers are unknowable."""
    action = step["action"]
    kind = action["type"]
    if kind == "navigate" and action.get("mode") == "observe":
        return {"level": "observation", "confirm": False, "reason": "Observe the recorded navigation"}
    if kind == "open_tab" and action.get("mode") == "observe":
        return {"level": "observation", "confirm": False, "reason": "Observe the tab opened by the previous action"}
    target = step.get("target") or {}
    words = " ".join(str(target.get(k, "")) for k in ("name", "text", "input_type"))
    if target.get("is_submit") or re.search(r"\b(submit|delete|remove|pay|purchase|checkout|confirm order|send|buy)\b", words, re.I):
        return {"level": "risky", "confirm": True, "reason": "May submit, delete, send, or pay"}
    return {"level": "unknown", "confirm": True, "reason": "The page can attach side effects to this action"}


def make_step(raw, page, frame):
    step = {"id": uid(), "at": raw.get("at") or now(), "action": raw["action"],
            "target": raw.get("target"), "page": page, "frame": frame,
            "nearby_text": raw.get("nearby_text", "")[:300],
            "screenshot": {"status": "pending", "phase": "after", "data_url": None},
            "label": {"template": "", "custom": None}}
    step["label"]["template"] = template(step)
    step["safety"] = safety(step)
    return step


def label(step):
    return step["label"].get("custom") or step["label"]["template"]


def selected_steps(recording, ids):
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids):
        raise ValueError("Select at least one step")
    selected = set(ids)
    result = [s for s in recording["steps"] if s["id"] in selected]
    if len(result) != len(selected):
        raise ValueError("Some selected steps no longer exist")
    return result


def export_text(recording, steps):
    lines = [recording["title"], ""]
    for i, step in enumerate(steps, 1):
        lines.append(f'{i}. {label(step)}')
        if step["page"].get("title"):
            lines.append(f'   Page: {step["page"]["title"]}')
    return "\n".join(lines) + "\n"


def _text(value, maximum=MAX_TEXT):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ValueError("Invalid or oversized text field")
    return value


def validate_target(target):
    if not isinstance(target, dict):
        raise ValueError("Missing element target")
    for key in ("role", "name", "text", "selector", "tag", "input_type"):
        _text(target.get(key, ""), 4096)
    if target.get("input_type", "").lower() == "password" or target.get("secret"):
        raise ValueError("Password targets are not allowed")
    selector = target.get("selector", "")
    if not selector or ">>" in selector or selector.startswith(("xpath=", "text=", "internal:", "javascript:")):
        raise ValueError("Target must contain a CSS selector")


def validate_frame(path):
    if not isinstance(path, list) or len(path) > 12:
        raise ValueError("Invalid frame path")
    for frame in path:
        validate_target(frame)


def validate_recording(data):
    """Do not accept executable code, file paths, or stored confirmation bypasses."""
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise ValueError("Unsupported recording version")
    _text(data.get("title"), 200)
    validate_url(data.get("start_url"))
    steps = data.get("steps")
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise ValueError("Invalid or oversized steps list")
    seen = set()
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("Invalid step")
        step_id = _text(step.get("id"), 64)
        if step_id in seen:
            raise ValueError("Duplicate step ID")
        seen.add(step_id)
        action = step.get("action", {})
        if not isinstance(action, dict):
            raise ValueError("Invalid action object")
        kind = action.get("type")
        if kind not in ACTIONS:
            raise ValueError("Unsupported action")
        if kind == "navigate":
            validate_url(action.get("url"))
            if action.get("mode", "goto") not in {"goto", "observe"}:
                raise ValueError("Invalid navigation mode")
        elif kind not in {"open_tab", "close_tab"}:
            validate_target(step.get("target"))
        if kind == "fill":
            _text(action.get("value"))
        if kind == "press":
            _text(action.get("key"), 100)
        if kind == "click":
            if action.get("button", "left") not in {"left", "middle", "right"}:
                raise ValueError("Invalid mouse button")
            modifiers = action.get("modifiers", [])
            if not isinstance(modifiers, list) or len(modifiers) > 4 or any(m not in {"Control", "Meta", "Alt", "Shift"} for m in modifiers):
                raise ValueError("Invalid keyboard modifiers")
        if kind == "check" and not isinstance(action.get("checked"), bool):
            raise ValueError("Expected a checkbox state")
        if kind == "select":
            values = action.get("values")
            if not isinstance(values, list) or len(values) > 100:
                raise ValueError("Invalid selection")
            for value in values:
                _text(value, 1000)
        if kind in {"upload", "drop_files"}:
            files = action.get("files")
            if not isinstance(files, list) or len(files) > 30:
                raise ValueError("Too many files")
            for file in files:
                _text(file.get("name"), 255)
                _text(file.get("mime", ""), 150)
                if not isinstance(file.get("size"), int) or file["size"] < 0:
                    raise ValueError("Invalid file size")
        if kind == "drag":
            validate_target(action.get("destination"))
            validate_frame(action.get("destination_frame", step.get("frame", [])))
            if action.get("strategy", "html5") not in {"html5", "pointer"}:
                raise ValueError("Invalid drag strategy")
            for field in ("source_position", "target_position", "delta"):
                if field in action:
                    coords = action[field]
                    if not isinstance(coords, dict) or any(not isinstance(coords.get(k), (int, float)) or not -2 <= coords[k] <= 2 for k in ("x", "y")):
                        raise ValueError("Invalid drag coordinates")
        page = step.get("page", {})
        if not isinstance(page, dict):
            raise ValueError("Invalid page object")
        _text(page.get("id"), 64)
        _text(page.get("url", ""), 8192)
        _text(page.get("title", ""), 1000)
        validate_frame(step.get("frame", []))
        _text(step.get("nearby_text", ""), 1000)
        labels = step.get("label", {})
        if not isinstance(labels, dict):
            raise ValueError("Invalid label object")
        _text(labels.get("template", ""), MAX_TEXT + 1000)
        if labels.get("custom") is not None:
            _text(labels["custom"], 2000)
        screenshot = step.get("screenshot", {})
        if not isinstance(screenshot, dict):
            raise ValueError("Invalid screenshot object")
        image = screenshot.get("data_url")
        if image is not None and (not isinstance(image, str) or not image.startswith("data:image/jpeg;base64,") or len(image) > 3000000):
            raise ValueError("Invalid screenshot")
        step["safety"] = safety(step)
    return data


def portable_copy(recording, ids=None):
    result = copy.deepcopy(recording)
    if ids is not None:
        result["steps"] = selected_steps(result, ids)
    return result
