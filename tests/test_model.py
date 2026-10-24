import copy
import json
import stat

import pytest

from model import (make_step, new_recording, validate_recording, validate_url, normalize_start_url,
                   selected_steps, export_text, safety)
from storage import Store


def sample():
    recording = new_recording("http://localhost:8765/mock", "Example")
    step = make_step({"action": {"type": "fill", "value": "October"},
        "target": {"role": "textbox", "name": "Report", "text": "", "tag": "input", "input_type": "text", "selector": "#report"}},
        {"id": "p1", "url": recording["start_url"], "title": "Reports"}, [])
    recording["steps"].append(step)
    return recording


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "ftp://example.com", "https://u:secret@example.com/", "http://", None])
def test_invalid_navigation_urls(url):
    with pytest.raises(ValueError):
        validate_url(url)


@pytest.mark.parametrize("typed, expected", [
    ("google.com", "https://google.com"),
    (" example.com/path?q=hello#section ", "https://example.com/path?q=hello#section"),
    ("https://example.com", "https://example.com"),
    ("//example.com", "https://example.com"),
    ("localhost:8765/mock", "http://localhost:8765/mock"),
    ("127.0.0.1:8765/mock", "http://127.0.0.1:8765/mock"),
    ("[::1]:8765/mock", "http://[::1]:8765/mock"),
    ("example.com:8443", "https://example.com:8443"),
])
def test_start_address_normalization(typed, expected):
    assert normalize_start_url(typed) == expected


@pytest.mark.parametrize("typed", ["", " ", None, "javascript:alert(1)",
    "file:///etc/passwd", "https://user:secret@example.com", "bad host.com",
    "https://example.com:99999", "http://"])
def test_start_address_rejects_invalid_input(typed):
    with pytest.raises(ValueError):
        normalize_start_url(typed)


def test_labels_do_not_change_actions_or_safety():
    recording = sample()
    step = recording["steps"][0]
    before = copy.deepcopy(step["action"])
    gate = safety(step)
    step["label"]["custom"] = "A harmless action"
    assert step["action"] == before and safety(step) == gate
    assert "A harmless action" in export_text(recording, [step])


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(version=200),
    lambda r: r["steps"][0]["action"].update(type="evaluate", code="alert(1)"),
    lambda r: r["steps"][0]["target"].update(input_type="password"),
    lambda r: r["steps"][0]["target"].update(selector="xpath=//input"),
    lambda r: r["steps"][0].update(screenshot={"data_url": "https://remote.example/track"}),
    lambda r: r["steps"].append(copy.deepcopy(r["steps"][0])),
    lambda r: r["steps"][0].update(action="invalid"),
    lambda r: r["steps"][0].update(label=[]),
])
def test_import_rejects_bad_or_executable_data(mutate):
    recording = sample()
    mutate(recording)
    with pytest.raises(ValueError):
        validate_recording(recording)


def test_selection_keeps_recorded_order():
    recording = sample()
    another = copy.deepcopy(recording["steps"][0])
    another["id"] = "second"
    recording["steps"].append(another)
    assert selected_steps(recording, ["second", recording["steps"][0]["id"]]) == recording["steps"]


def test_atomic_private_storage_and_reload(tmp_path):
    store = Store(tmp_path)
    recording = sample()
    store.save(recording)
    recording["steps"][0]["label"]["custom"] = "<script>not HTML</script>"
    store.save(recording)
    path = tmp_path / f'{recording["id"]}.json'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert Store(tmp_path).get(recording["id"])["steps"][0]["label"]["custom"] == "<script>not HTML</script>"
    assert not list(tmp_path.glob(".memact-*"))


def test_corrupt_file_is_reported_without_overwrite(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json")
    store = Store(tmp_path)
    assert store.load_errors and path.read_text() == "not json"


def test_import_recalculates_confirmation():
    recording = sample()
    recording["steps"][0]["safety"] = {"confirm": False}
    validate_recording(recording)
    assert recording["steps"][0]["safety"]["confirm"] is True
