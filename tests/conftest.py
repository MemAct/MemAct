import sys
from pathlib import Path

import pytest_asyncio
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import create_app, HOSTS, SESSION, STORE, TOKEN


@pytest_asyncio.fixture
async def local(tmp_path):
    app = create_app(tmp_path / "recordings", headless=True, port=0)
    server = TestServer(app)
    await server.start_server()
    app[HOSTS].update({f"127.0.0.1:{server.port}", f"localhost:{server.port}"})
    yield app, server
    await server.close()


@pytest_asyncio.fixture
async def recorded(local):
    app, server = local
    session = app[SESSION]
    recording = await session.start_recording(str(server.make_url("/mock")), "Test recording")
    await session.queue.join()
    yield session, recording, session.pages["p1"], app, server
