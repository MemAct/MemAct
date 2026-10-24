"""Public Playwright APIs for capture and guarded replay. No codegen internals."""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import time
from pathlib import Path

from playwright.async_api import async_playwright, Error as PlaywrightError

from model import (MAX_STEPS, make_step, new_recording, now, safety, selected_steps,
                   uid, validate_recording, validate_target, validate_url)

ROOT = Path(__file__).parent
SCRIPT = "(() => {\n" + (ROOT / "vendor/accessibility.js").read_text() + "\n" + (ROOT / "capture.js").read_text() + "\n})();"
MASK = '[data-memact-private], [data-memact-ignore], input[type="password"], input[autocomplete*="password"], input[autocomplete="one-time-code"]'


class BrowserSession:
    def __init__(self, store, headless=False):
        self.store = store
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.pages = {}
        self.page_ids = {}
        self.frame_cache = {}
        self.mode = "idle"
        self.recording_id = None
        self.message = ""
        self.pending = None
        self.replay_progress = None
        self.replacement = None
        self.queue = asyncio.Queue(maxsize=256)
        self.worker = None
        self.replay_task = None
        self.confirmation = None
        self.background = set()
        self.last_action = {}
        self.last_navigation = {}
        self.drag_source = None
        self.initializing = False
        self.unmapped_pages = []
        self.unmapped_event = asyncio.Event()
        self.replay_pages = {}
        self.dialog_failure = None

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        task.add_done_callback(self._background_result)
        return task

    def _background_result(self, task):
        if task.cancelled():
            return
        error = task.exception()
        if error:
            self.message = f"Browser event could not be captured: {type(error).__name__}"

    async def ensure_browser(self, fresh=False):
        if fresh and self.context:
            await self.context.close()
            self.context = None
        if not self.playwright:
            self.playwright = await async_playwright().start()
        if not self.browser or not self.browser.is_connected():
            self.browser = await self.playwright.chromium.launch(headless=self.headless, channel="chromium")
        if not self.context:
            self.pages = {}
            self.page_ids = {}
            self.frame_cache = {}
            self.last_action = {}
            self.last_navigation = {}
            self.context = await self.browser.new_context(viewport={"width": 1280, "height": 800}, accept_downloads=False)
            self.context.set_default_timeout(5000)
            await self.context.expose_binding("__memactEmit", self.binding)
            await self.context.add_init_script(script=SCRIPT)
            self.context.on("page", self.register_page)
        if not self.worker or self.worker.done():
            self.worker = asyncio.create_task(self.consume())

    def register_page(self, page):
        if page in self.page_ids:
            return
        page_id = f"p{len(self.page_ids) + 1}"
        self.page_ids[page] = page_id
        self.pages[page_id] = page
        page.on("framenavigated", lambda frame: self.spawn(self.navigation(page, frame)))
        page.on("domcontentloaded", lambda: self.spawn(self.ensure_capture(page)))
        page.on("close", lambda: self.spawn(self.page_closed(page, page_id)))
        page.on("dialog", lambda dialog: self.spawn(self.dismiss_dialog(dialog)))
        page.on("download", lambda _: self.notice("A download was triggered. Downloads are not stored in the recording."))
        if self.mode in {"replaying", "confirm"}:
            self.unmapped_pages.append(page)
            self.unmapped_event.set()
        elif not self.initializing:
            self.spawn(self.page_opened(page, page_id))

    def notice(self, message):
        self.message = message

    async def dismiss_dialog(self, dialog):
        await dialog.dismiss()
        self.message = "The page opened a native dialog. It was dismissed; record a page-based alternative."
        if self.mode in {"replaying", "confirm"}:
            self.dialog_failure = "A native browser dialog was dismissed. This action needs manual handling."

    async def frame_path(self, frame):
        if frame == frame.page.main_frame:
            return []
        path = []
        current = frame
        try:
            while current.parent_frame:
                handle = await current.frame_element()
                descriptor = await handle.evaluate("el => window.__memact.describe(el)")
                await handle.dispose()
                if not descriptor:
                    raise ValueError("Cannot identify this iframe")
                descriptor["frame_url"] = current.url
                descriptor["frame_name"] = current.name
                path.insert(0, descriptor)
                current = current.parent_frame
            self.frame_cache[frame] = path
            return path
        except (PlaywrightError, ValueError):
            if frame in self.frame_cache:
                return self.frame_cache[frame]
            raise ValueError("The frame detached before it could be identified") from None

    async def binding(self, source, raw):
        if self.mode not in {"recording", "rerecording"}:
            return
        if not isinstance(raw, dict) or len(json.dumps(raw)) > 65000:
            self.notice("Ignored an oversized browser event")
            return
        page, frame = source["page"], source["frame"]
        if page not in self.page_ids:
            return
        try:
            path = await self.frame_path(frame)
            await self.enqueue(page, frame, raw, path)
        except ValueError as error:
            self.notice(str(error))

    async def enqueue(self, page, frame, raw, path=None):
        if self.queue.full():
            self.mode = "error"
            self.notice("Recording stopped because events arrived faster than they could be saved")
            return
        if path is None:
            path = await self.frame_path(frame)
        self.queue.put_nowait((page, frame, raw, copy.deepcopy(path)))

    async def page_opened(self, page, page_id):
        if self.mode != "recording":
            return
        opener = await page.opener()
        raw = {"action": {"type": "open_tab", "mode": "observe" if opener else "create",
                          "opener_id": self.page_ids.get(opener)},
               "url": page.url, "title": "New tab", "time": time.time() * 1000}
        await self.enqueue(page, page.main_frame, raw, [])
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
            await self.ensure_capture(page)
            key = (page, page.main_frame)
            if page.url.startswith(("http://", "https://")) and self.last_navigation.get(key) != page.url:
                self.last_navigation[key] = page.url
                await self.enqueue(page, page.main_frame, {"action": {"type": "navigate",
                    "mode": "observe" if opener else "goto", "url": page.url},
                    "url": page.url, "title": await page.title()}, [])
        except PlaywrightError:
            self.notice("The new tab could not finish loading")

    async def ensure_capture(self, page):
        if page.is_closed():
            return
        for frame in page.frames:
            try:
                installed = await frame.evaluate("window.__memact?.document === document")
                if not installed:
                    await frame.evaluate(SCRIPT)
            except PlaywrightError:
                pass

    async def page_closed(self, page, page_id):
        if self.mode == "recording":
            await self.enqueue(page, page.main_frame, {
                "action": {"type": "close_tab"}, "url": page.url, "title": "Closed tab"}, [])

    async def navigation(self, page, frame):
        await self.ensure_capture(page)
        if self.initializing or self.mode != "recording" or not frame.url.startswith(("http://", "https://")):
            return
        key = (page, frame)
        if self.last_navigation.get(key) == frame.url:
            return
        self.last_navigation[key] = frame.url
        last = self.last_action.get(page)
        caused_by = last[0] if last and time.monotonic() - last[1] < 3 else None
        try:
            path = await self.frame_path(frame)
            # Newly opened tabs and iframe navigation are effects, not extra goto calls.
            opener = await page.opener()
            mode = "observe" if caused_by or opener or path else "goto"
            await self.enqueue(page, frame, {"action": {"type": "navigate", "mode": mode,
                "url": frame.url, "caused_by": caused_by}, "url": page.url,
                "title": "", "time": time.time() * 1000}, path)
        except ValueError as error:
            self.notice(str(error))

    async def consume(self):
        while True:
            page, frame, raw, path = await self.queue.get()
            try:
                await self.accept(page, frame, raw, path)
            except (ValueError, KeyError, TypeError, OSError) as error:
                self.mode = "error"
                self.notice(f"Recording stopped: {error}")
            except PlaywrightError:
                self.notice("An event's page disappeared before it could be captured")
            finally:
                self.queue.task_done()

    async def accept(self, page, frame, raw, path):
        if not self.recording_id or self.mode not in {"recording", "rerecording", "stopping"}:
            return
        action = raw.get("action", {})
        if not isinstance(action, dict) or (raw.get("target") is not None and not isinstance(raw["target"], dict)):
            raise ValueError("Invalid browser event")
        kind = action.get("type")
        if kind == "drag_start":
            self.drag_source = (page, frame, raw, path)
            return
        if kind == "drag_end":
            if not self.drag_source:
                self.notice("External non-file drops are not recorded. Drag an element or select files.")
                return
            src_page, src_frame, src, src_path = self.drag_source
            self.drag_source = None
            if src_page != page:
                self.notice("A cross-tab drag could not be recorded")
                return
            action = {"type": "drag", "strategy": "html5", "destination": raw.get("target"),
                      "destination_frame": path, "source_position": src["action"].get("source_position", {"x": .5, "y": .5}),
                      "target_position": action.get("target_position", {"x": .5, "y": .5})}
            raw = dict(src, action=action)
            frame, path = src_frame, src_path
        if action.get("type") == "drag":
            action.setdefault("destination_frame", path)
        if (raw.get("target") or {}).get("input_type") == "password":
            return
        recording = self.store.get(self.recording_id)
        if len(recording["steps"]) >= MAX_STEPS:
            raise ValueError("Reached 2,000 steps. Stop and start another recording.")
        title = raw.get("title", "")
        top_url = page.url
        if not page.is_closed():
            try:
                title = await page.title() if not title or path else title
            except PlaywrightError:
                pass
        page_meta = {"id": self.page_ids[page], "url": raw.get("url") if not path else top_url,
                     "title": title[:1000], "frame_url": raw.get("url", "")}
        step = make_step(raw, page_meta, path)
        # Reuse the import validator on the one-step object so hostile page data cannot
        # introduce new executable action types or invalid targets into the file.
        validate_recording({"version": 1, "title": "event", "start_url": recording["start_url"], "steps": [step]})
        if self.mode == "rerecording":
            old = next(s for s in recording["steps"] if s["id"] == self.replacement)
            step["id"] = old["id"]
            step["label"]["custom"] = old["label"].get("custom")
            recording["steps"][recording["steps"].index(old)] = step
            self.mode = "idle"
            self.replacement = None
            self.notice("Step replaced. Review the following steps before replaying.")
        else:
            recording["steps"].append(step)
        if action["type"] not in {"navigate", "open_tab", "close_tab"}:
            self.last_action[page] = (step["id"], time.monotonic())
        self.store.save(recording)
        await self.screenshot(page, step)
        self.store.save(recording)

    async def screenshot(self, page, step):
        try:
            if page.is_closed():
                raise ValueError("Tab closed")
            masks = []
            for frame in page.frames:
                try:
                    await frame.evaluate("window.__memact?.markSecrets()")
                    masks.append(frame.locator(MASK))
                except PlaywrightError:
                    pass
            image = await page.screenshot(type="jpeg", quality=58, scale="css", timeout=2000,
                mask=masks, mask_color="#171717", style=MASK + " { visibility: hidden !important; }")
            step["screenshot"] = {"status": "captured", "phase": "after", "captured_at": now(),
                "url": page.url, "data_url": "data:image/jpeg;base64," + base64.b64encode(image).decode()}
        except (PlaywrightError, ValueError):
            step["screenshot"] = {"status": "unavailable", "phase": "after", "data_url": None}

    async def start_recording(self, url, title):
        self.require_idle()
        validate_url(url)
        await self.queue.join()
        self.initializing = True
        try:
            await self.ensure_browser(fresh=True)
            recording = new_recording(url, title or "Untitled recording")
            self.store.save(recording)
            self.recording_id = recording["id"]
            self.mode = "recording"
            page = await self.context.new_page()
            self.register_page(page)
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await self.enqueue(page, page.main_frame, {"action": {"type": "navigate", "mode": "goto", "url": url},
                "url": url, "title": await page.title()}, [])
            self.last_navigation[(page, page.main_frame)] = page.url
            self.notice("Recording in the browser window")
            return recording
        except Exception:
            self.mode = "idle"
            raise
        finally:
            self.initializing = False

    async def flush(self):
        if self.context:
            for page in self.context.pages:
                for frame in page.frames:
                    try:
                        await frame.evaluate("async () => { await window.__memact?.flush(); }")
                    except PlaywrightError:
                        pass
        await self.queue.join()

    async def stop_recording(self):
        if self.mode not in {"recording", "rerecording", "preparing", "error"}:
            raise ValueError("No recording to stop")
        if self.mode in {"recording", "rerecording"}:
            await self.flush()
        self.mode = "idle"
        self.replacement = None
        self.drag_source = None
        self.notice("Saved locally")

    def require_idle(self):
        if self.mode not in {"idle", "error"}:
            raise ValueError("Finish the current recording or replay first")

    async def prepare_replacement(self, recording_id, step_id):
        self.require_idle()
        recording = self.store.get(recording_id)
        original = next((s for s in recording["steps"] if s["id"] == step_id), None)
        if not original:
            raise ValueError("Step not found")
        await self.ensure_browser()
        self.recording_id = recording_id
        self.replacement = step_id
        self.mode = "preparing"
        candidates = [p for p in self.context.pages if not p.is_closed()]
        page = candidates[0] if candidates else await self.context.new_page()
        if page.url == "about:blank":
            await page.goto(validate_url(original["page"].get("url") or recording["start_url"]), wait_until="domcontentloaded")
        await page.bring_to_front()
        self.notice("Restore the browser to the state before this step, then choose Record next action")

    async def arm_replacement(self):
        if self.mode != "preparing":
            raise ValueError("No step is waiting to be re-recorded")
        for page in self.context.pages:
            for frame in page.frames:
                try:
                    await frame.evaluate("window.__memact?.reset()")
                except PlaywrightError:
                    pass
        self.mode = "rerecording"
        self.notice("The next action will replace this step")

    async def resolve_frame(self, page, path):
        frame = page.main_frame
        for descriptor in path:
            locator = frame.locator("css=" + descriptor["selector"])
            await locator.wait_for(state="attached", timeout=5000)
            if await locator.count() != 1:
                raise ValueError("The iframe selector is ambiguous")
            handle = await locator.element_handle()
            child = await handle.content_frame()
            await handle.dispose()
            if not child:
                raise ValueError("The expected iframe is not attached")
            frame = child
        return frame

    async def resolve_target(self, frame, target):
        validate_target(target)
        role, name = target.get("role"), target.get("name", "")
        semantic = frame.get_by_role(role, name=name, exact=True) if role and name else None
        css = frame.locator("css=" + target["selector"])
        deadline = time.monotonic() + 4
        while True:
            count = await semantic.count() if semantic else 0
            if count == 1:
                candidate = semantic
                break
            css_count = await css.count()
            if css_count == 1:
                if count > 1:
                    intersection = semantic.and_(css)
                    if await intersection.count() != 1:
                        raise ValueError("Role/name and CSS disagree about the target")
                    candidate = intersection
                else:
                    candidate = css
                break
            if time.monotonic() >= deadline:
                raise ValueError("Element is missing or ambiguous. Restore the page or re-record this step.")
            await asyncio.sleep(.08)
        live = await candidate.evaluate("""el => ({tag: el.localName, type: el.localName === 'input' ? el.type : '',
            secret: el.matches('[data-memact-private],[data-memact-ignore]') || !!el.closest('[data-memact-private],[data-memact-ignore]') || /password|one-time-code/.test(el.autocomplete || '')})""")
        if live["secret"] or live["type"] == "password":
            raise ValueError("Refusing to interact with a password or private field")
        if live["tag"] != target.get("tag") or live["type"] != target.get("input_type", ""):
            raise ValueError("The target changed element or input type")
        identity = await candidate.evaluate("el => window.__memact?.describe(el)")
        if identity:
            if target.get("role") and identity.get("role") != target["role"]:
                raise ValueError("The target changed its accessible role")
            if target.get("name") and identity.get("name") != target["name"]:
                raise ValueError("The target changed its accessible name")
        return candidate

    async def start_replay(self, recording_id, ids):
        self.require_idle()
        recording = self.store.get(recording_id)
        steps = copy.deepcopy(selected_steps(recording, ids))
        # Replay an immutable snapshot. Labels are never parsed as instructions.
        validate_recording({**recording, "steps": steps})
        self.mode = "replaying"
        self.pending = None
        self.recording_id = recording_id
        self.message = "Replay starting"
        self.replay_task = asyncio.create_task(self.replay(steps))

    async def replay(self, steps):
        try:
            # A full sequence starts from its own navigation. A subset can use the
            # existing prepared browser; missing tabs always stop explicitly.
            if steps[0]["action"]["type"] == "navigate" and steps[0]["action"].get("mode") != "observe":
                await self.ensure_browser(fresh=True)
                page = await self.context.new_page()
                self.unmapped_pages.clear()
                self.replay_pages = {steps[0]["page"]["id"]: page}
            else:
                await self.ensure_browser()
                self.replay_pages = {key: value for key, value in self.pages.items() if not value.is_closed()}
                self.unmapped_pages.clear()
                if not self.replay_pages:
                    raise ValueError("Replay this sequence from its opening step, or prepare a browser before replaying a subset")
            for index, step in enumerate(steps):
                self.replay_progress = {"index": index + 1, "total": len(steps), "step_id": step["id"]}
                gate = safety(step)
                files = []
                if gate["confirm"]:
                    files = await self.wait_confirmation(step, gate)
                self.mode = "replaying"
                await self.perform(step, files)
                if self.dialog_failure:
                    raise ValueError(self.dialog_failure)
            self.notice(f"Replayed {len(steps)} steps")
        except asyncio.CancelledError:
            self.notice("Replay cancelled. Actions already performed cannot be undone.")
        except (ValueError, PlaywrightError) as error:
            # Do not retry with another selector after an uncertain side effect.
            detail = str(error).split("\n")[0][:250]
            self.notice(f"Replay stopped at step {(self.replay_progress or {}).get('index', '?')}: {detail}")
        finally:
            self.pending = None
            self.confirmation = None
            self.mode = "idle"

    async def wait_confirmation(self, step, gate):
        self.confirmation = asyncio.get_running_loop().create_future()
        token = uid()
        self.pending = {"token": token, "step_id": step["id"], "label": step["label"].get("custom") or step["label"]["template"],
                        "action": step["action"], "target": step.get("target"), "page": step["page"],
                        "reason": gate["reason"], "level": gate["level"],
                        "needs_files": step["action"]["type"] in {"upload", "drop_files"} and bool(step["action"].get("files"))}
        self.mode = "confirm"
        files = await self.confirmation
        self.pending = None
        self.confirmation = None
        return files

    def confirm(self, token, files=None):
        if not self.pending or token != self.pending["token"] or not self.confirmation or self.confirmation.done():
            raise ValueError("This confirmation is stale or has already been used")
        supplied = files or []
        if self.pending["needs_files"]:
            expected = self.pending["action"]["files"]
            if len(supplied) != len(expected):
                raise ValueError("Choose the same number of files as in the recorded step")
            for item, original in zip(supplied, expected):
                if item.get("name") != original["name"] or len(item.get("buffer", b"")) != original["size"]:
                    raise ValueError("File names and sizes must match the recorded step")
        self.confirmation.set_result(supplied)
        self.mode = "replaying"

    async def cancel_replay(self):
        if not self.replay_task or self.replay_task.done():
            raise ValueError("No replay to cancel")
        self.replay_task.cancel()
        await self.replay_task

    async def perform(self, step, files):
        self.dialog_failure = None
        action = step["action"]
        kind = action["type"]
        page_id = step["page"]["id"]
        if kind == "open_tab":
            if action.get("mode") == "observe":
                deadline = time.monotonic() + 5
                while not self.unmapped_pages:
                    if time.monotonic() > deadline:
                        raise ValueError("The previous action did not open the expected tab")
                    self.unmapped_event.clear()
                    try:
                        await asyncio.wait_for(self.unmapped_event.wait(), timeout=.2)
                    except asyncio.TimeoutError:
                        pass
                page = self.unmapped_pages.pop(0)
            else:
                page = await self.context.new_page()
                if page in self.unmapped_pages:
                    self.unmapped_pages.remove(page)
            self.replay_pages[page_id] = page
            await page.bring_to_front()
            return
        page = self.replay_pages.get(page_id)
        if not page or page.is_closed():
            raise ValueError(f"Tab {page_id} is unavailable. Include its opening step or restore the tab.")
        await page.bring_to_front()
        if kind == "close_tab":
            await page.close()
            return
        frame = await self.resolve_frame(page, step.get("frame", []))
        if kind == "navigate":
            url = validate_url(action["url"])
            if action.get("mode") == "observe":
                await frame.wait_for_url(url, timeout=8000, wait_until="domcontentloaded")
            else:
                await frame.goto(url, wait_until="domcontentloaded", timeout=20000)
            return
        expected_url = step["page"].get("frame_url") or (step["frame"][-1].get("frame_url") if step.get("frame") else step["page"].get("url"))
        if expected_url and frame.url != expected_url:
            raise ValueError("The browser is on a different page. Restore it before replaying this step.")
        locator = await self.resolve_target(frame, step["target"])
        if kind == "click":
            await locator.click(button=action.get("button", "left"), modifiers=action.get("modifiers", []), timeout=5000)
        elif kind == "fill":
            await locator.fill(action["value"], timeout=5000)
            # Captured fills are committed edits, so reproduce the change/blur too.
            await locator.blur(timeout=2000)
        elif kind == "press":
            await locator.press(action["key"], timeout=5000)
        elif kind == "check":
            await locator.set_checked(action["checked"], timeout=5000)
        elif kind == "select":
            await locator.select_option(action["values"], timeout=5000)
        elif kind == "upload":
            await locator.set_input_files(files, timeout=5000)
        elif kind == "drop_files":
            await locator.drop({"files": files}, timeout=5000)
        elif kind == "drag":
            dest_frame = await self.resolve_frame(page, action.get("destination_frame", step.get("frame", [])))
            destination = await self.resolve_target(dest_frame, action["destination"])
            source_box, target_box = await locator.bounding_box(), await destination.bounding_box()
            if not source_box or not target_box:
                raise ValueError("Drag source or destination is not visible")
            start = action.get("source_position", {"x": .5, "y": .5})
            end = action.get("target_position", {"x": .5, "y": .5})
            source_pos = {"x": source_box["width"] * start["x"], "y": source_box["height"] * start["y"]}
            target_pos = {"x": target_box["width"] * end["x"], "y": target_box["height"] * end["y"]}
            if action.get("strategy") == "pointer":
                if step["target"]["selector"] == action["destination"]["selector"] and action.get("delta"):
                    viewport = page.viewport_size or {"width": 1280, "height": 800}
                    target_box = source_box
                    target_pos = {"x": source_pos["x"] + action["delta"]["x"] * viewport["width"],
                                  "y": source_pos["y"] + action["delta"]["y"] * viewport["height"]}
                await page.mouse.move(source_box["x"] + source_pos["x"], source_box["y"] + source_pos["y"])
                await page.mouse.down()
                try:
                    await page.mouse.move(target_box["x"] + target_pos["x"], target_box["y"] + target_pos["y"], steps=18)
                finally:
                    await page.mouse.up()
            else:
                await locator.drag_to(destination, source_position=source_pos, target_position=target_pos, timeout=5000)
        if self.dialog_failure:
            raise ValueError(self.dialog_failure)

    async def close(self):
        self.mode = "idle"
        if self.replay_task and not self.replay_task.done():
            self.replay_task.cancel()
            await self.replay_task
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
        for task in list(self.background):
            task.cancel()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
