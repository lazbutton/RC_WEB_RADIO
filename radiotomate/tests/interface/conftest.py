import os
import secrets
import sqlite3
import time
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from subprocess import Popen, run
from tempfile import TemporaryDirectory

import pytest
import requests
from playwright.async_api import Browser, BrowserContext, Page

from radiotomate.db import QuartAlchemy
from tests.interface import ADMIN_USERNAME, USER_PASSWORD, USER_USERNAME
from tests.interface.models import ensure_connected

admin_password = secrets.token_hex(16)
CONTEXT_DEFAULT_TIMEOUT = 5_000


@pytest.fixture(scope="session", autouse=True)
def server_root() -> Generator[Path, None, None]:
    """
    End-to-end tests start a headless browser process that needs to do requests
    to a real HTTP server, so we also create a whole new development instance
    and start a sub-process with that server. The fixture returns the path
    to the instance's working directory.
    """
    with TemporaryDirectory() as tmpdir:
        print(  # noqa:T201
            f"Using temporary directory {tmpdir},"
            f"{ADMIN_USERNAME} password is {admin_password}"
        )
        os.chdir(tmpdir)
        config_path = "radio_data/radiotomate.yaml"
        run(["radiotomate", "develop", "--quiet"], check=True)
        run(
            [
                "radiotomate",
                "--config-path",
                config_path,
                "users",
                "add",
                ADMIN_USERNAME,
                "--password",
                admin_password,
                "--admin",
            ],
            check=True,
        )
        run(
            [
                "radiotomate",
                "--config-path",
                config_path,
                "users",
                "add",
                USER_USERNAME,
                "--password",
                USER_PASSWORD,
            ],
            check=True,
        )
        server = Popen(["radiotomate", "-c", config_path, "interface", "--demo"])

        # wait a bit until the server is available
        for _ in range(1000):
            try:
                requests.get("http://127.0.0.1:6811")
                break
            except requests.exceptions.ConnectionError:
                time.sleep(0.01)
        else:
            raise RuntimeError("Interface server is not responding. Aborting.")

        yield Path(tmpdir)
        server.kill()


@pytest.fixture
def db_cursor(server_root: Path) -> Generator[sqlite3.Cursor, None, None]:
    db = sqlite3.connect(server_root / "radio_data/radiotomate.db")
    cursor = db.cursor()
    QuartAlchemy.sqlite_pragmas(cursor)
    yield cursor
    db.commit()
    db.close()


@pytest.fixture
async def context(browser: Browser) -> AsyncGenerator[BrowserContext]:
    ctx = await browser.new_context(base_url="http://127.0.0.1:6811")
    ctx.set_default_timeout(CONTEXT_DEFAULT_TIMEOUT)
    yield ctx
    await ctx.close()


@pytest.fixture
async def luser_page(context: BrowserContext) -> AsyncGenerator[Page]:
    page = await context.new_page()
    await page.goto("/login")
    await page.get_by_label("Identifiant").fill(USER_USERNAME)
    await page.get_by_label("Mot de passe").fill(USER_PASSWORD)
    await page.get_by_label("Mot de passe").press("Enter")
    await ensure_connected(page)
    yield page
    await page.get_by_role("navigation").get_by_role("link", name="Déconnexion").click()
    await page.close()


@pytest.fixture
async def admin_context(browser: Browser) -> AsyncGenerator[BrowserContext]:
    ctx = await browser.new_context(base_url="http://127.0.0.1:6811")
    ctx.set_default_timeout(CONTEXT_DEFAULT_TIMEOUT)
    page = await ctx.new_page()
    await page.goto("/login")
    await page.get_by_label("Identifiant").fill(ADMIN_USERNAME)
    await page.get_by_label("Mot de passe").fill(admin_password)
    await page.get_by_label("Mot de passe").press(
        "Enter",
    )  # clicking on the button is tested by test_users.py
    await ensure_connected(page)
    yield ctx
    await ctx.close()


@pytest.fixture
async def admin_page(admin_context: BrowserContext) -> AsyncGenerator[Page]:
    page = admin_context.pages[0]
    yield page
    await page.close()
