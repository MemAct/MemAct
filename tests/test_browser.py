import asyncio
import copy
import json
import time

import pytest

from model import label, safety
from server import SESSION


async def until(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for browser state")
        await asyncio.sleep(.03)


async def settled(session):
    await asyncio.sleep(.08)  # Allow browser event notifications to reach the binding.
    await session.flush()


async def test_closing_last_tab_stops_recording_and_preserves_steps(recorded):
    session, recording, page, app, server = recorded
    await page.get_by_label("Report name", exact=True).fill("Before closing")
    await settled(session)
    other = await session.context.new_page()
    await other.goto(str(server.make_url("/mock")))
    await settled(session)
    await page.close()
    await until(lambda: any(s["action"]["type"] == "close_tab" for s in recording["steps"]))
    assert session.mode == "recording"
    await other.close()
    await until(lambda: session.mode == "idle")
    assert any(s["action"].get("value") == "Before closing" for s in recording["steps"])
    assert len([s for s in recording["steps"] if s["action"]["type"] == "close_tab"]) == 2
    saved = json.loads((session.store.directory / f'{recording["id"]}.json').read_text())
    assert saved["steps"] == recording["steps"]
    assert session.queue.empty()


async def test_browser_shutdown_stops_recording_and_allows_restart(recorded):
    session, recording, page, app, server = recorded
    await page.get_by_label("Report name", exact=True).fill("Saved before exit")
    await settled(session)
    await session.browser.close()
    await until(lambda: session.mode == "idle")
    assert session.context is None
    assert any(s["action"].get("value") == "Saved before exit" for s in recording["steps"])
    restarted = await session.start_recording(str(server.make_url("/mock")), "Restarted")
    await settled(session)
    assert session.mode == "recording" and restarted["id"] != recording["id"]
    await session.stop_recording()


async def test_replay_asks_before_opening_window_and_then_navigates(recorded):
    session, recording, page, *_ = recorded
    await session.stop_recording()
    context = session.context
    initial = recording["steps"][0]
    await session.start_replay(recording["id"], [initial["id"]])
    await until(lambda: session.pending is not None)
    assert session.context is context and not page.is_closed()
    assert all(tab.url != "about:blank" for tab in context.pages)
    session.confirm(session.pending["token"])
    await session.replay_task
    assert session.mode == "idle" and session.message == "Replayed 1 steps"
    assert session.replay_pages[initial["page"]["id"]].url == initial["action"]["url"]


async def replay_all(session, recording, files=None, ids=None):
    await session.start_replay(recording["id"], ids or [step["id"] for step in recording["steps"]])
    used = set()
    deadline = time.monotonic() + 40
    while not session.replay_task.done():
        if time.monotonic() > deadline:
            raise AssertionError(f"Replay timed out: {session.message}")
        if session.pending and session.pending["token"] not in used:
            used.add(session.pending["token"])
            session.confirm(session.pending["token"], files if session.pending["needs_files"] else [])
        await asyncio.sleep(.03)
    assert session.message.startswith("Replayed"), session.message


async def test_input_then_enter_submits_once(recorded):
    session, recording, page, *_ = recorded
    await page.get_by_label("Report name", exact=True).fill("October")
    await page.get_by_label("Report name", exact=True).press("Enter")
    await session.stop_recording()
    actions = [s["action"]["type"] for s in recording["steps"] if s["action"]["type"] != "navigate"]
    assert actions == ["fill", "press"], actions
    assert all(s["screenshot"]["status"] == "captured" for s in recording["steps"])
    await replay_all(session, recording)
    replay_page = session.replay_pages["p1"]
    assert await replay_page.evaluate("effects.saved") == 1
    assert await replay_page.get_by_label("Report name", exact=True).input_value() == "October"


async def test_final_empty_edit_saved_on_stop(recorded):
    session, recording, page, *_ = recorded
    await page.get_by_label("Report name", exact=True).fill("temporary")
    await page.get_by_label("Report name", exact=True).fill("")
    await session.stop_recording()
    fills = [s for s in recording["steps"] if s["action"]["type"] == "fill"]
    assert len(fills) == 1 and fills[0]["action"]["value"] == ""


async def test_checkbox_select_and_shortcut(recorded):
    session, recording, page, *_ = recorded
    await page.get_by_label("Include archived items").check()
    await page.get_by_label("Period", exact=True).press("ArrowDown")
    await page.get_by_role("button", name="Shortcut target", exact=True).press("Control+k")
    await session.stop_recording()
    kinds = [s["action"]["type"] for s in recording["steps"]]
    assert "check" in kinds and "select" in kinds and "press" in kinds
    await replay_all(session, recording)
    replay_page = session.replay_pages["p1"]
    assert await replay_page.get_by_label("Include archived items").is_checked()
    assert await replay_page.get_by_label("Period", exact=True).input_value() == "week"
    assert await replay_page.evaluate("effects.shortcut") == 1


async def test_passwords_excluded_after_reveal_and_paste(recorded):
    session, recording, page, *_ = recorded
    await page.get_by_label("Password", exact=True).fill("Secret-alpha-716")
    await page.get_by_role("button", name="Show password").click()
    await page.get_by_label("Password", exact=True).fill("Secret-beta-821")
    await page.get_by_label("Password", exact=True).press("Control+k")
    await page.get_by_role("button", name="Export", exact=True).click()
    await session.stop_recording()
    encoded = json.dumps(recording)
    assert "Secret-alpha" not in encoded and "Secret-beta" not in encoded
    assert all((s.get("target") or {}).get("selector") != "#secret-field" for s in recording["steps"])
    assert await page.locator("#secret-field").get_attribute("data-memact-private") == ""


async def test_role_name_survives_id_change_and_duplicate_is_scoped(recorded):
    session, recording, page, *_ = recorded
    await page.get_by_role("button", name="Export", exact=True).click()
    await page.locator("#second-row button").click()
    await session.stop_recording()
    click = next(s for s in recording["steps"] if (s.get("target") or {}).get("name") == "Export")
    session.replay_pages = dict(session.pages)
    await page.locator("#export-button").evaluate("el => el.id = 'new-export-id'")
    await session.perform(click, [])
    assert await page.evaluate("effects.exported") == 2
    second = next(s for s in recording["steps"] if (s.get("target") or {}).get("name") == "Open report")
    await session.perform(second, [])
    assert await page.locator("#notice").inner_text() == "second-row"
    ambiguous = copy.deepcopy(second["target"])
    ambiguous["selector"] = ".duplicate"
    with pytest.raises(ValueError, match="missing or ambiguous"):
        await session.resolve_target(page.main_frame, ambiguous)


async def test_css_fallback_and_changed_identity_rejected(recorded):
    session, recording, page, *_ = recorded
    await page.get_by_role("button", name="Export", exact=True).click()
    await session.stop_recording()
    click = next(s for s in recording["steps"] if s["action"]["type"] == "click")
    target = copy.deepcopy(click["target"])
    target["role"] = ""; target["name"] = ""
    assert await (await session.resolve_target(page.main_frame, target)).count() == 1
    await page.locator("#export-button").evaluate("el => { el.setAttribute('aria-label', 'Delete everything'); }")
    with pytest.raises(ValueError, match="accessible name"):
        await session.resolve_target(page.main_frame, click["target"])


async def test_navigation_is_observed_not_duplicated(recorded):
    session, recording, page, *_ = recorded
    await page.get_by_role("link", name="Next page", exact=True).click()
    await settled(session)
    await session.stop_recording()
    navigations = [s for s in recording["steps"] if s["action"]["type"] == "navigate" and not s["frame"]]
    assert len(navigations) == 2
    assert navigations[1]["action"]["mode"] == "observe"
    await replay_all(session, recording)
    assert session.replay_pages["p1"].url.endswith("/mock/done")


async def test_spa_navigation_and_cross_origin_frame(recorded):
    session, recording, page, app, server = recorded
    await page.locator("#details-frame").evaluate("(el,url) => el.src = url", f"http://localhost:{server.port}/mock/frame")
    frame = page.frame_locator("#details-frame")
    await frame.get_by_label("Detail note").fill("Inside a different origin")
    await frame.get_by_role("button", name="Save details").click()
    await page.get_by_role("button", name="Change route").click()
    await settled(session)
    await session.stop_recording()
    fill = next(s for s in recording["steps"] if s["action"]["type"] == "fill")
    assert fill["frame"][0]["selector"] == "#details-frame"
    assert "localhost" in fill["page"]["frame_url"]
    assert any(s["action"]["type"] == "navigate" and "route=reports" in s["action"]["url"] for s in recording["steps"])
    # Replay the recorded frame actions in the still-prepared cross-origin frame.
    session.replay_pages = dict(session.pages)
    await page.evaluate("history.replaceState({}, '', '/mock')")
    frame_steps = [s for s in recording["steps"] if s["frame"] and s["action"]["type"] != "navigate"]
    for step in frame_steps:
        await session.perform(step, [])
    assert await frame.locator("#frame-notice").inner_text() == "Saved: Inside a different origin"


async def test_popup_and_return_to_opener(recorded):
    session, recording, page, *_ = recorded
    async with page.expect_popup() as info:
        await page.get_by_role("link", name="Open details in new tab").click()
    popup = await info.value
    await popup.wait_for_load_state("domcontentloaded")
    await popup.get_by_label("Report name", exact=True).fill("In a popup")
    await popup.get_by_role("button", name="Export", exact=True).click()
    await page.get_by_role("button", name="Export", exact=True).click()
    await settled(session)
    await session.stop_recording()
    assert any(s["action"]["type"] == "open_tab" for s in recording["steps"])
    assert len({s["page"]["id"] for s in recording["steps"]}) == 2
    await replay_all(session, recording)
    assert await session.replay_pages["p1"].evaluate("effects.exported") == 1
    assert await session.replay_pages["p2"].evaluate("effects.exported") == 1


async def test_upload_reselects_bytes_without_saving_them(recorded, tmp_path):
    session, recording, page, *_ = recorded
    file = {"name": "sample.txt", "mimeType": "text/plain", "buffer": b"private file contents"}
    original = tmp_path / "sample.txt"
    original.write_bytes(file["buffer"])
    # Native file selection emits trusted events; in-memory Playwright fixtures
    # synthesize DOM events and are deliberately not treated as user recording.
    await page.get_by_label("Attachments", exact=True).set_input_files(str(original))
    await session.stop_recording()
    uploads = [s for s in recording["steps"] if s["action"]["type"] == "upload"]
    assert len(uploads) == 1
    assert uploads[0]["action"]["files"] == [{"name": "sample.txt", "mime": "text/plain", "size": 21}]
    assert "private file contents" not in json.dumps(recording)
    await replay_all(session, recording, files=[file])
    assert await session.replay_pages["p1"].locator("#uploaded-files").inner_text() == "sample.txt"


async def test_html_drag_and_pointer_slider(recorded):
    session, recording, page, *_ = recorded
    await page.locator("#drag-item").drag_to(page.locator("#drag-destination"))
    thumb = page.locator("#slider-thumb")
    await thumb.scroll_into_view_if_needed()
    box = await thumb.bounding_box()
    await page.mouse.move(box["x"] + 20, box["y"] + 20)
    await page.mouse.down()
    await page.mouse.move(box["x"] + 170, box["y"] + 20, steps=15)
    await page.mouse.up()
    await session.stop_recording()
    drags = [s for s in recording["steps"] if s["action"]["type"] == "drag"]
    assert {s["action"]["strategy"] for s in drags} == {"html5", "pointer"}
    await replay_all(session, recording)
    replay_page = session.replay_pages["p1"]
    assert await replay_page.locator("#drag-destination").inner_text() == "Report card moved"
    assert int(await replay_page.locator("#slider-thumb").get_attribute("aria-valuenow")) > 100


async def test_external_file_drop(recorded, tmp_path):
    session, recording, page, *_ = recorded
    file_path = tmp_path / "dropped.txt"
    file_path.write_text("drop contents")
    target = page.locator("#file-drop")
    await target.scroll_into_view_if_needed()
    box = await target.bounding_box()
    cdp = await page.context.new_cdp_session(page)
    data = {"items": [], "files": [str(file_path)], "dragOperationsMask": 1}
    for kind in ["dragEnter", "dragOver", "drop"]:
        await cdp.send("Input.dispatchDragEvent", {"type": kind, "x": box["x"]+30, "y": box["y"]+30, "data": data})
    await session.stop_recording()
    drops = [s for s in recording["steps"] if s["action"]["type"] == "drop_files"]
    assert len(drops) == 1
    payload = {"name": "dropped.txt", "mimeType": "text/plain", "buffer": b"drop contents"}
    await replay_all(session, recording, files=[payload])
    assert await session.replay_pages["p1"].locator("#file-drop").inner_text() == "Files: dropped.txt"


async def test_confirmation_cancel_and_stale_token(recorded):
    session, recording, page, *_ = recorded
    await page.get_by_role("button", name="Delete report").click()
    await session.stop_recording()
    await session.start_replay(recording["id"], [s["id"] for s in recording["steps"]])
    await until(lambda: session.pending is not None)
    first = session.pending["token"]
    session.confirm(first)
    with pytest.raises(ValueError, match="stale"):
        session.confirm(first)
    await until(lambda: session.pending is not None and session.pending["token"] != first)
    assert session.pending["level"] == "risky"
    await session.cancel_replay()
    assert await session.replay_pages["p1"].evaluate("effects.deleted") == 0


async def test_rerecord_replaces_exactly_one_step(recorded):
    session, recording, page, *_ = recorded
    await page.get_by_role("button", name="Export", exact=True).click()
    await session.stop_recording()
    step = next(s for s in recording["steps"] if s["action"]["type"] == "click")
    original_ids = [s["id"] for s in recording["steps"]]
    step["label"]["custom"] = "My label"
    await session.prepare_replacement(recording["id"], step["id"])
    await page.get_by_label("Report name", exact=True).fill("Setup is not recorded")
    await session.arm_replacement()
    await page.get_by_role("button", name="Save report", exact=True).click()
    await until(lambda: session.mode == "idle")
    await session.queue.join()
    assert [s["id"] for s in recording["steps"]] == original_ids
    replacement = next(s for s in recording["steps"] if s["id"] == step["id"])
    assert replacement["target"]["name"] == "Save report"
    assert label(replacement) == "My label" and safety(replacement)["confirm"]
    assert not any(s["action"]["type"] == "fill" for s in recording["steps"])


async def test_native_dialog_stops_replay_instead_of_claiming_success(recorded):
    session, recording, page, *_ = recorded
    await page.get_by_role("button", name="Export", exact=True).click()
    await page.get_by_role("button", name="Delete report", exact=True).click()
    await session.stop_recording()
    await page.evaluate("() => {effects.deleted = 0; document.querySelector('#export-button').onclick = () => confirm('Export now?');}")
    clicks = [s["id"] for s in recording["steps"] if s["action"]["type"] == "click"]
    await session.start_replay(recording["id"], clicks)
    await until(lambda: session.pending is not None)
    session.confirm(session.pending["token"])
    await until(lambda: session.replay_task.done())
    assert "Replay stopped" in session.message and "native browser dialog" in session.message
    assert await page.evaluate("effects.deleted") == 0

async def test_failed_entire_rerecord_keeps_original(recorded, monkeypatch):
    session, recording, page, *_ = recorded
    await session.stop_recording()
    original = copy.deepcopy(recording)

    async def unavailable_browser(**kwargs):
        raise ValueError('Browser could not open')

    monkeypatch.setattr(session, 'ensure_browser', unavailable_browser)
    with pytest.raises(ValueError, match='Browser could not open'):
        await session.start_recording(recording['start_url'], recording['title'], replace_id=recording['id'])
    assert session.store.get(recording['id'])['steps'] == original['steps']
    assert session.mode == 'idle'
