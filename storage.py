"""One private, atomically replaced JSON file per recording."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from model import now, uid, validate_recording


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.recordings = {}
        self.revision = 0
        self.load_errors = []
        for path in self.directory.glob("*.json"):
            try:
                if path.stat().st_size > 100 * 1024 * 1024:
                    raise ValueError("Recording exceeds 100 MB")
                data = validate_recording(json.loads(path.read_text()))
                if not re.fullmatch(r"[a-f0-9]{32}", data["id"]) or path.stem != data["id"]:
                    raise ValueError("Invalid recording ID")
                self.recordings[data["id"]] = data
            except (ValueError, KeyError, TypeError, OSError) as error:
                self.load_errors.append(f"Could not load {path.name}: {error}")

    def get(self, recording_id):
        try:
            return self.recordings[recording_id]
        except KeyError:
            raise ValueError("Recording not found") from None

    def save(self, data):
        recording_id = data["id"]
        if not re.fullmatch(r"[a-f0-9]{32}", recording_id):
            raise ValueError("Invalid recording ID")
        data["updated_at"] = now()
        encoded = json.dumps(data, ensure_ascii=False, indent=2)
        if len(encoded.encode()) > 100 * 1024 * 1024:
            raise ValueError("Recording reached the 100 MB local file limit")
        fd, temporary = tempfile.mkstemp(prefix=".memact-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / f"{recording_id}.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.recordings[recording_id] = data
        self.revision += 1

    def import_recording(self, data):
        data = validate_recording(data)
        data["id"] = uid()
        self.save(data)
        return data

    def delete(self, recording_id):
        self.get(recording_id)
        (self.directory / f"{recording_id}.json").unlink()
        del self.recordings[recording_id]
        self.revision += 1

    def summaries(self):
        return [{k: data[k] for k in ("id", "title", "created_at", "updated_at", "start_url")} |
                {"step_count": len(data["steps"])} for data in
                sorted(self.recordings.values(), key=lambda r: r["updated_at"], reverse=True)]
