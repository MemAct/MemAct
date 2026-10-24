"""Loopback-only HTTP API and plain static timeline."""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import os
import secrets
from pathlib import Path

from aiohttp import web
from playwright.async_api import Error as PlaywrightError

from browser import BrowserSession
from model import export_text, label, normalize_start_url, portable_copy, selected_steps
from storage import Store

ROOT = Path(__file__).parent
STORE = web.AppKey("store", Store)
SESSION = web.AppKey("session", BrowserSession)
TOKEN = web.AppKey("token", str)
LOCK = web.AppKey("lock", asyncio.Lock)
HOSTS = web.AppKey("hosts", set)


@web.middleware
async def boundary(request, handler):
    if request.host not in request.app[HOSTS]:
        return web.json_response({"error": "Invalid Host header"}, status=403)
    origin = request.headers.get("Origin")
    if origin and origin not in {f"http://{host}" for host in request.app[HOSTS]}:
        return web.json_response({"error": "Cross-origin requests are not allowed"}, status=403)
    if request.path.startswith("/api/"):
        token = request.headers.get("X-MemAct-Token", "")
        if not secrets.compare_digest(token, request.app[TOKEN]):
            return web.json_response({"error": "Missing local session token. Reload MemAct."}, status=403)
        if request.method != "GET" and not origin:
            return web.json_response({"error": "Origin header required"}, status=403)
    try:
        response = await handler(request)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        response = web.json_response({"error": str(error)[:300]}, status=400)
    except PlaywrightError as error:
        # Playwright launch errors are useful without dumping a recorded action log.
        message = str(error).split("Call log:")[0][:800]
        response = web.json_response({"error": "Browser error: " + message}, status=400)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if not request.path.startswith("/mock"):
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'"
    return response


async def body(request):
    if request.content_type != "application/json":
        raise ValueError("Expected application/json")
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


async def index(request):
    html = (ROOT / "web/index.html").read_text().replace("__SESSION_TOKEN__", request.app[TOKEN])
    return web.Response(text=html, content_type="text/html")


async def state(request):
    session = request.app[SESSION]
    return web.json_response({"revision": request.app[STORE].revision, "mode": session.mode,
        "recording_id": session.recording_id, "recordings": request.app[STORE].summaries(),
        "pending": session.pending, "message": session.message, "progress": session.replay_progress,
        "replacement": session.replacement, "load_errors": request.app[STORE].load_errors})


async def get_recording(request):
    return web.json_response(request.app[STORE].get(request.match_info["recording_id"]))


async def start(request):
    data = await body(request)
    url = normalize_start_url(data.get("url"))
    title = data.get("title", "Untitled recording")
    if not isinstance(title, str) or len(title) > 200:
        raise ValueError("Use a recording name of 200 characters or fewer")
    async with request.app[LOCK]:
        recording = await request.app[SESSION].start_recording(url, title)
    return web.json_response({"id": recording["id"]})


async def stop(request):
    async with request.app[LOCK]:
        await request.app[SESSION].stop_recording()
    return web.json_response({"ok": True})


async def edit(request):
    data = await body(request)
    async with request.app[LOCK]:
        request.app[SESSION].require_idle()
        recording = request.app[STORE].get(request.match_info["recording_id"])
        if "title" in data:
            title = data["title"]
            if not isinstance(title, str) or not title.strip() or len(title) > 200:
                raise ValueError("Use a name of 1 to 200 characters")
            recording["title"] = title.strip()
        elif "step_id" in data:
            step = next((s for s in recording["steps"] if s["id"] == data["step_id"]), None)
            if not step:
                raise ValueError("Step not found")
            custom = data.get("label")
            if custom is not None and (not isinstance(custom, str) or len(custom) > 2000):
                raise ValueError("Step labels must be under 2,000 characters")
            step["label"]["custom"] = custom.strip() or None if custom is not None else None
        else:
            raise ValueError("Nothing to edit")
        request.app[STORE].save(recording)
    return web.json_response({"ok": True})


async def delete_steps(request):
    data = await body(request)
    async with request.app[LOCK]:
        request.app[SESSION].require_idle()
        recording = request.app[STORE].get(request.match_info["recording_id"])
        removed = {s["id"] for s in selected_steps(recording, data.get("ids"))}
        recording["steps"] = [s for s in recording["steps"] if s["id"] not in removed]
        request.app[STORE].save(recording)
    return web.json_response({"ok": True})


async def delete_recording(request):
    await body(request)
    async with request.app[LOCK]:
        request.app[SESSION].require_idle()
        request.app[STORE].delete(request.match_info["recording_id"])
    return web.json_response({"ok": True})


async def export(request):
    data = await body(request)
    recording = request.app[STORE].get(request.match_info["recording_id"])
    steps = selected_steps(recording, data.get("ids"))
    if data.get("format") == "text":
        return web.Response(text=export_text(recording, steps), content_type="text/plain")
    return web.json_response(portable_copy(recording, data.get("ids")))


async def import_recording(request):
    data = await body(request)
    async with request.app[LOCK]:
        request.app[SESSION].require_idle()
        result = request.app[STORE].import_recording(data)
    return web.json_response({"id": result["id"]})


async def replay(request):
    data = await body(request)
    async with request.app[LOCK]:
        await request.app[SESSION].start_replay(data.get("recording_id"), data.get("ids"))
    return web.json_response({"ok": True})


async def confirm(request):
    data = await body(request)
    files = data.get("files", [])
    if not isinstance(files, list) or len(files) > 30:
        raise ValueError("Invalid file list")
    buffers = []
    total = 0
    for file in files:
        if not isinstance(file, dict) or not isinstance(file.get("data"), str) or len(file["data"]) > 35000000:
            raise ValueError("Invalid upload payload")
        try:
            buffer = base64.b64decode(file["data"], validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Invalid file encoding") from None
        total += len(buffer)
        if total > 25 * 1024 * 1024:
            raise ValueError("Replay supports up to 25 MB of reselected files per step")
        name = file.get("name", "")
        mime = file.get("mime", "application/octet-stream")
        if not isinstance(name, str) or not name or "/" in name or "\\" in name or len(name) > 255 or not isinstance(mime, str):
            raise ValueError("Invalid file name")
        buffers.append({"name": name, "mimeType": mime, "buffer": buffer})
    request.app[SESSION].confirm(data.get("token"), buffers)
    return web.json_response({"ok": True})


async def cancel(request):
    await request.app[SESSION].cancel_replay()
    return web.json_response({"ok": True})


async def rerecord(request):
    data = await body(request)
    async with request.app[LOCK]:
        await request.app[SESSION].prepare_replacement(data.get("recording_id"), data.get("step_id"))
    return web.json_response({"ok": True})


async def arm(request):
    await request.app[SESSION].arm_replacement()
    return web.json_response({"ok": True})


async def mock(request):
    return web.FileResponse(ROOT / "web/mock.html")


async def cleanup(app):
    await app[SESSION].close()


def create_app(data_dir=None, headless=False, port=8765):
    app = web.Application(middlewares=[boundary], client_max_size=100 * 1024 * 1024)
    app[STORE] = Store(data_dir or ROOT / "data")
    app[SESSION] = BrowserSession(app[STORE], headless=headless)
    app[TOKEN] = secrets.token_urlsafe(32)
    app[LOCK] = asyncio.Lock()
    app[HOSTS] = {f"127.0.0.1:{port}", f"localhost:{port}"}
    app.router.add_get("/", index)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/recordings/{recording_id}", get_recording)
    app.router.add_post("/api/recordings/{recording_id}/edit", edit)
    app.router.add_post("/api/recordings/{recording_id}/delete", delete_steps)
    app.router.add_post("/api/recordings/{recording_id}/remove", delete_recording)
    app.router.add_post("/api/recordings/{recording_id}/export", export)
    app.router.add_post("/api/import", import_recording)
    app.router.add_post("/api/record/start", start)
    app.router.add_post("/api/record/stop", stop)
    app.router.add_post("/api/replay/start", replay)
    app.router.add_post("/api/replay/confirm", confirm)
    app.router.add_post("/api/replay/cancel", cancel)
    app.router.add_post("/api/rerecord/prepare", rerecord)
    app.router.add_post("/api/rerecord/arm", arm)
    app.router.add_get("/mock", mock)
    app.router.add_get("/mock/frame", mock)
    app.router.add_get("/mock/done", mock)
    app.router.add_static("/assets", ROOT / "web/assets", show_index=False)
    app.router.add_static("/static", ROOT / "web", show_index=False)
    app.on_cleanup.append(cleanup)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MemAct local recorder")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--headless", action="store_true", help="For automated local tests")
    args = parser.parse_args()
    os.umask(0o077)
    print(f"MemAct: http://127.0.0.1:{args.port}")
    web.run_app(create_app(args.data_dir, args.headless, args.port), host="127.0.0.1", port=args.port, print=None, access_log=None)
