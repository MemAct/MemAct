import asyncio
import copy
import json
from pathlib import Path

import aiohttp
import pytest

from model import new_recording, make_step
from server import TOKEN, STORE, SESSION


async def test_new_recording_accepts_bare_domain_and_shows_inline_errors(local, monkeypatch):
    app, server = local
    session = app[SESSION]
    await session.ensure_browser()
    page = await session.context.new_page()
    started = []

    async def fake_start(url, title):
        started.append(url)
        recording = new_recording(url, title)
        app[STORE].save(recording)
        return recording

    monkeypatch.setattr(session, "start_recording", fake_start)
    await page.goto(str(server.make_url("/")))
    await page.locator("#new-recording-button").click()
    await page.locator("#start-button").click()
    await page.locator("#record-error:not([hidden])").wait_for()
    assert "Enter a web address" in await page.locator("#record-error").inner_text()
    assert not started
    await page.locator("#new-url").fill("bad host.com")
    async with page.expect_response(lambda response: response.url.endswith("/api/record/start")) as response:
        await page.locator("#start-button").click()
    assert (await response.value).status == 400
    await page.locator("#record-error:not([hidden])").wait_for()
    assert await page.locator("#new-url").input_value() == "bad host.com"
    assert not started
    await page.locator("#new-url").fill("google.com")
    async with page.expect_response(lambda response: response.url.endswith("/api/record/start")) as response:
        await page.locator("#start-button").click()
    assert (await response.value).status == 200
    await page.locator("#record-dialog").wait_for(state="hidden")
    assert started == ["https://google.com"]


async def test_server_rejects_foreign_requests_and_missing_token(local):
    app, server = local
    url = str(server.make_url("/api/state"))
    async with aiohttp.ClientSession() as client:
        async with client.get(url) as response:
            assert response.status == 403
        async with client.get(url, headers={"X-MemAct-Token": app[TOKEN], "Origin": "https://evil.example"}) as response:
            assert response.status == 403
        async with client.get(url, headers={"X-MemAct-Token": app[TOKEN], "Host": "evil.example"}) as response:
            assert response.status == 403
        async with client.post(str(server.make_url("/api/record/stop")), headers={"X-MemAct-Token": app[TOKEN]}, json={}) as response:
            assert response.status == 403
        async with client.get(url, headers={"X-MemAct-Token": app[TOKEN]}) as response:
            assert response.status == 200
            assert "Access-Control-Allow-Origin" not in response.headers


async def test_recording_menus_rename_cancel_and_delete(local):
    app, server = local
    first = new_recording(str(server.make_url("/mock")), "October report")
    second = new_recording(str(server.make_url("/mock")), "Another recording")
    app[STORE].save(first)
    app[STORE].save(second)
    session = app[SESSION]
    await session.ensure_browser()
    page = await session.context.new_page()
    await page.goto(str(server.make_url("/")))
    row = page.locator(f'.recording-row[data-id="{first["id"]}"]')
    await row.locator(".recording-link").click(button="right")
    await page.get_by_role("menu", name="Recording actions").wait_for()
    await page.get_by_role("menuitem", name="Rename", exact=True).click()
    await page.get_by_label("Name", exact=True).fill("Renamed report")
    async with page.expect_response(lambda r: r.url.endswith("/edit")) as saved:
        await page.get_by_role("button", name="Save", exact=True).click()
    assert (await saved.value).status == 200
    await row.get_by_role("button", name="Renamed report", exact=False).first.wait_for()
    assert app[STORE].get(first["id"])["title"] == "Renamed report"
    assert "Renamed report" in (app[STORE].directory / f'{first["id"]}.json').read_text()
    await row.locator(".recording-link").click()
    await page.locator("#recording-options").click()
    await page.get_by_role("menuitem", name="Delete recording").click()
    await page.locator("#cancel-recording-action").click()
    assert app[STORE].get(first["id"])
    await row.hover()
    assert await row.locator(".history-more").evaluate("el => getComputedStyle(el).opacity") == "1"
    await row.locator(".history-more").click()
    await page.keyboard.press("Escape")
    assert await page.locator("#recording-menu").is_hidden()
    await page.locator("#recording-options").click()
    await page.get_by_role("menuitem", name="Delete recording").click()
    async with page.expect_response(lambda r: r.url.endswith("/remove")) as removed:
        await page.locator("#save-recording-action").click()
    assert (await removed.value).status == 200
    await row.wait_for(state="detached")
    assert first["id"] not in app[STORE].recordings
    assert not (app[STORE].directory / f'{first["id"]}.json').exists()
    assert app[STORE].get(second["id"])
    await page.wait_for_function("() => document.querySelector('#recording-name').value === 'Another recording'")
    session.mode = "recording"
    headers = {"X-MemAct-Token": app[TOKEN], "Origin": str(server.make_url("")).rstrip("/")}
    async with aiohttp.ClientSession(headers=headers) as client:
        async with client.post(str(server.make_url(f'/api/recordings/{second["id"]}/remove')), json={}) as response:
            assert response.status == 400
    session.mode = "idle"
    assert app[STORE].get(second["id"])


async def test_edit_delete_export_and_roundtrip(recorded):
    session, recording, page, app, server = recorded
    await page.get_by_label("Report name", exact=True).fill("October")
    await page.get_by_role("button", name="Export", exact=True).click()
    await session.stop_recording()
    headers = {"X-MemAct-Token": app[TOKEN], "Origin": str(server.make_url("")).rstrip("/")}
    base = str(server.make_url(f'/api/recordings/{recording["id"]}'))
    selected = recording["steps"][-1]
    async with aiohttp.ClientSession(headers=headers) as client:
        async with client.post(base + "/edit", json={"step_id": selected["id"], "label": "Download the report"}) as response:
            assert response.status == 200
        async with client.post(base + "/export", json={"ids": [selected["id"]], "format": "text"}) as response:
            text = await response.text()
            assert "Download the report" in text
            assert "Entered" not in text
        async with client.post(base + "/export", json={"ids": [s["id"] for s in recording["steps"]], "format": "json"}) as response:
            portable = await response.json()
            assert portable["steps"][-1]["screenshot"]["data_url"].startswith("data:image/jpeg;base64,")
        async with client.post(str(server.make_url("/api/import")), json=portable) as response:
            assert response.status == 200
            imported_id = (await response.json())["id"]
            assert imported_id != recording["id"]
        assert app[STORE].get(imported_id)["steps"] == portable["steps"]
        async with client.post(base + "/delete", json={"ids": [selected["id"]]}) as response:
            assert response.status == 200
        assert selected["id"] not in {s["id"] for s in recording["steps"]}


async def test_ui_search_edit_and_xss_are_safe(local):
    app, server = local
    session = app[SESSION]
    recording = new_recording(str(server.make_url("/mock")), "October reports")
    target = {"selector": "#export-button", "role": "button", "name": "Export", "text": "Export", "tag": "button", "input_type": ""}
    first = make_step({"action": {"type": "click"}, "target": target},
        {"id": "p1", "url": recording["start_url"], "title": "Sample workspace"}, [])
    first["label"]["custom"] = '<img src=x onerror="window.__xss=true">'
    first["screenshot"] = {"status": "unavailable", "data_url": None}
    second = copy.deepcopy(first)
    second["id"] = "second-step"
    second["label"]["custom"] = "Download October report"
    recording["steps"] = [first, second]
    app[STORE].save(recording)
    await session.ensure_browser()
    page = await session.context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.goto(str(server.make_url("/")))
    await page.get_by_label("Recording name", exact=True).wait_for()
    assert await page.locator("#timeline li").count() == 2
    assert await page.evaluate("window.__xss === undefined")
    await page.get_by_label("Search steps", exact=True).fill("October")
    assert await page.locator("#timeline li").count() == 1
    await page.get_by_label("Label for step 2", exact=True).fill("Get the report")
    async with page.expect_response(lambda response: response.url.endswith("/edit")) as saved:
        await page.get_by_label("Label for step 2", exact=True).press("Enter")
    assert (await saved.value).status == 200
    assert recording["steps"][1]["label"]["custom"] == "Get the report"
    await page.reload()
    await page.get_by_label("Recording name", exact=True).wait_for()
    assert await page.get_by_label("Label for step 2", exact=True).input_value() == "Get the report"
    await page.get_by_label("Search recordings", exact=True).fill("No match")
    assert await page.locator("#recording-list button").count() == 0
    assert not errors


async def test_visual_contract_on_desktop_and_mobile(local):
    app, server = local
    session = app[SESSION]
    await session.ensure_browser()
    page = await session.context.new_page()
    await page.goto(str(server.make_url("/")))
    await page.evaluate("document.fonts.ready")
    for width in [1280, 424]:
        await page.set_viewport_size({"width": width, "height": 800})
        result = await page.evaluate("""() => {
            const logo=document.querySelector('.brand img').getBoundingClientRect();
            const search=document.querySelector('#library-search').getBoundingClientRect();
            const icon=document.querySelector('.library-search-icon').getBoundingClientRect();
            return {bg:getComputedStyle(document.body).backgroundColor,font:getComputedStyle(document.body).fontFamily,
                visibleLogoLeft:logo.x+186*logo.width/952, searchLeft:search.x,
                iconRight:icon.right, searchRight:search.right,
                overflow:document.documentElement.scrollWidth>innerWidth};
        }""")
        assert result["bg"] == "rgb(0, 1, 27)"
        assert "DM Sans" in result["font"]
        assert abs(result["visibleLogoLeft"] - result["searchLeft"]) < 1
        assert result["searchRight"] - 32 < result["iconRight"] < result["searchRight"]
        assert not result["overflow"]


async def test_history_collapses_and_remembers_preference(local):
    app, server = local
    app[STORE].save(new_recording(str(server.make_url("/mock")), "October report"))
    session = app[SESSION]
    await session.ensure_browser()
    page = await session.context.new_page()
    await page.goto(str(server.make_url("/")))
    await page.locator("#recording-options").wait_for()
    sizes = [await page.locator(selector).bounding_box() for selector in
        ["#library-search", "#new-recording-button", ".recording-link"]]
    assert max(box["width"] for box in sizes) - min(box["width"] for box in sizes) < 1
    assert max(box["height"] for box in sizes) - min(box["height"] for box in sizes) < 1
    expanded_icon = await page.locator("#toggle-history path").get_attribute("d")
    before = await page.locator("main").bounding_box()
    await page.get_by_role("button", name="Hide recording history", exact=True).click()
    assert await page.locator("#recording-history").is_visible()
    assert await page.locator("#recording-list").is_hidden()
    assert await page.locator("#rail-search").is_visible()
    assert await page.locator("#rail-history").is_visible()
    assert await page.locator("#new-recording-button").is_visible()
    assert await page.locator("#toggle-history path").get_attribute("d") != expanded_icon
    after = await page.locator("main").bounding_box()
    assert after["width"] > before["width"] + 150
    await page.reload()
    assert await page.locator("#recording-list").is_hidden()
    await page.locator("#rail-search").click()
    assert await page.locator("#library-search").evaluate("el => document.activeElement === el")
    assert await page.locator("#recording-list").is_visible()
    await page.set_viewport_size({"width": 424, "height": 800})
    await page.get_by_role("button", name="Hide recording history", exact=True).click()
    assert await page.locator("#recording-list").is_hidden()
    assert not await page.evaluate("document.documentElement.scrollWidth > innerWidth")
    await page.locator("#rail-history").click()
    assert await page.locator("#recording-list").is_visible()


async def test_step_deletion_uses_native_memact_dialog(local):
    app, server = local
    recording = new_recording(str(server.make_url("/mock")), "October report")
    for index in range(3):
        recording["steps"].append(make_step({"action": {"type": "click"},
            "target": {"selector": "#export-button", "role": "button", "name": "Export", "tag": "button"}},
            {"id": "p1", "url": recording["start_url"], "title": "Sample workspace"}, []))
    app[STORE].save(recording)
    session = app[SESSION]
    await session.ensure_browser()
    page = await session.context.new_page()
    browser_dialogs = []
    page.on("dialog", lambda dialog: browser_dialogs.append(dialog.type))
    await page.goto(str(server.make_url("/")))
    await page.get_by_label("Select step 1", exact=True).check()
    await page.locator("#delete-button").click()
    await page.get_by_role("dialog", name="Delete 1 selected step?").wait_for()
    assert await page.locator("#cancel-delete-steps").evaluate("el => el === document.activeElement")
    await page.locator("#cancel-delete-steps").click()
    assert len(recording["steps"]) == 3
    await page.locator("#delete-button").click()
    await page.keyboard.press("Escape")
    assert await page.locator("#delete-steps-dialog").is_hidden()
    assert len(recording["steps"]) == 3
    await page.get_by_label("Select step 2", exact=True).check()
    await page.locator("#delete-button").click()
    await page.get_by_role("dialog", name="Delete 2 selected steps?").wait_for()
    kept_id = recording["steps"][2]["id"]
    async with page.expect_response(lambda response: response.url.endswith("/delete")) as deleted:
        await page.locator("#confirm-delete-steps").click()
    assert (await deleted.value).status == 200
    await page.wait_for_function("() => document.querySelectorAll('#timeline li').length === 1")
    assert [step["id"] for step in recording["steps"]] == [kept_id]
    assert not browser_dialogs


async def test_export_dialog_downloads_only_selected_steps(local):
    app, server = local
    recording = new_recording(str(server.make_url("/mock")), "October report")
    for name in ["Export", "Save"]:
        recording["steps"].append(make_step({"action": {"type": "click"},
            "target": {"selector": "#export-button", "role": "button", "name": name, "tag": "button"}},
            {"id": "p1", "url": recording["start_url"], "title": "Sample workspace"}, []))
    app[STORE].save(recording)
    session = app[SESSION]
    await session.ensure_browser()
    page = await session.browser.new_page(accept_downloads=True)
    await page.goto(str(server.make_url("/")))
    assert await page.locator("#export-button").is_disabled()
    await page.get_by_label("Select step 1", exact=True).check()
    await page.locator("#export-button").click()
    await page.get_by_role("dialog", name="Export 1 selected step as").wait_for()
    await page.locator("#cancel-export").click()
    assert await page.locator("#export-dialog").is_hidden()
    await page.locator("#export-button").click()
    await page.keyboard.press("Escape")
    assert await page.locator("#export-dialog").is_hidden()
    for format in ["json", "text"]:
        await page.locator("#export-button").click()
        async with page.expect_download() as download:
            await page.locator(f'#export-{format}').click()
        file = await download.value
        content = Path(await file.path()).read_text()
        assert file.suggested_filename.endswith(".json" if format == "json" else ".txt")
        if format == "json":
            assert [step["id"] for step in json.loads(content)["steps"]] == [recording["steps"][0]["id"]]
        else:
            assert "Export" in content and "Save" not in content
        await page.locator("#export-dialog").wait_for(state="hidden")

    await page.close()

async def test_long_history_and_timeline_scroll_independently(local):
    app, server = local
    for index in range(40):
        app[STORE].save(new_recording(str(server.make_url("/mock")), f"Recording {index}"))
    long = new_recording(str(server.make_url("/mock")), "Long procedure")
    target = {"selector": "#export-button", "role": "button", "name": "Export", "tag": "button", "input_type": ""}
    for index in range(35):
        step = make_step({"action": {"type": "click"}, "target": target},
            {"id": "p1", "url": long["start_url"], "title": "Sample workspace"}, [])
        step["screenshot"] = {"status": "unavailable", "data_url": None}
        long["steps"].append(step)
    app[STORE].save(long)
    session = app[SESSION]
    await session.ensure_browser()
    page = await session.context.new_page()
    await page.goto(str(server.make_url("/")))
    await page.wait_for_function("() => document.querySelectorAll('#recording-list .recording-link').length === 41 && document.querySelectorAll('#timeline li').length === 35")
    before = await page.evaluate("""() => {
        const history=document.querySelector('#recording-list'),timeline=document.querySelector('.timeline-wrap');
        const search=document.querySelector('#library-search').getBoundingClientRect();
        const button=document.querySelector('#new-recording-button').getBoundingClientRect();
        return {historyScrollable:history.scrollHeight>history.clientHeight,
            timelineScrollable:timeline.scrollHeight>timeline.clientHeight,
            historyBottom:history.getBoundingClientRect().bottom,height:innerHeight,
            ordered:search.bottom<=button.top && button.bottom<=history.getBoundingClientRect().top,
            colors:[history,timeline,document.querySelector('.inspector')].map(el=>getComputedStyle(el).scrollbarColor)};
    }""")
    sizes = [await page.locator(selector).first.bounding_box() for selector in
        ["#library-search", "#new-recording-button", ".recording-link"]]
    assert max(box["width"] for box in sizes) - min(box["width"] for box in sizes) < 1
    assert max(box["height"] for box in sizes) - min(box["height"] for box in sizes) < 1
    assert before["historyScrollable"] and before["timelineScrollable"]
    assert before["historyBottom"] <= before["height"] and before["ordered"]
    assert len(set(before["colors"])) == 1 and before["colors"][0] != "auto"
    await page.locator("#recording-list").evaluate("el => el.scrollTop=300")
    assert await page.locator(".timeline-wrap").evaluate("el => el.scrollTop") == 0
    await page.get_by_role("button", name="New recording", exact=True).click()
    assert await page.get_by_role("dialog").is_visible()
    assert await page.get_by_label("Page URL", exact=True).is_visible()
