from copy import deepcopy

from sqlalchemy.orm import Session as ormSession

from radiotomate.beets import BeetsIntegration
from radiotomate.interface.csrf import HEADER_NAME
from radiotomate.interface_app import app_factory as interface_factory
from tests.scheduler.test_admin_json import _admin_client

CLOCK_BODY = {
    "name": "CSRF clock",
    "fallback_cart": "Jingles",
    "motif": [
        {
            "kind": "jingle",
            "cart": "Jingles",
            "category": None,
            "fallback_cart": "Jingles",
        }
    ],
    "anchors": [],
}


def _csrf_config(app_configration: dict) -> dict:
    config = deepcopy(app_configration)
    interface = dict(config.get("interface") or {})
    interface["csrf_enabled"] = True
    interface["cookie_secure"] = False
    config["interface"] = interface
    return config


def test_cookie_secure_follows_config(
    app_configration: dict,
    beets_integration: BeetsIntegration,
):
    secure = deepcopy(app_configration)
    secure["interface"] = dict(secure.get("interface") or {})
    secure["interface"]["cookie_secure"] = True
    app = interface_factory(secure, False, beets_integration)
    assert app.config["QUART_AUTH_COOKIE_SECURE"] is True

    insecure = deepcopy(app_configration)
    insecure["interface"] = dict(insecure.get("interface") or {})
    insecure["interface"]["cookie_secure"] = False
    app = interface_factory(insecure, False, beets_integration)
    assert app.config["QUART_AUTH_COOKIE_SECURE"] is False


async def test_login_is_exempt_from_csrf(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    from radiotomate.models import User

    config = _csrf_config(app_configration)
    user = User(username="csrf-login")
    user.update_password(users_password)
    dbsession.add(user)
    await dbsession.commit()
    client = interface_factory(config, False, beets_integration).test_client()
    html = await client.post(
        "/login",
        form={"username": "csrf-login", "password": users_password},
    )
    assert html.status_code == 302
    json_login = await client.post(
        "/login.json",
        json={"username": "csrf-login", "password": users_password},
    )
    assert json_login.status_code == 200


async def test_mutation_requires_csrf_token(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    jingles_cart,
):
    client = await _admin_client(
        _csrf_config(app_configration),
        beets_integration,
        dbsession,
        users_password,
        "csrf-admin",
    )
    denied = await client.post("/autodj/clocks.json", json=CLOCK_BODY)
    assert denied.status_code == 403
    payload = await denied.get_json()
    assert payload["error"] == "csrf_invalid"

    wrong = await client.post(
        "/autodj/clocks.json",
        json=CLOCK_BODY,
        headers={HEADER_NAME: "deadbeef"},
    )
    assert wrong.status_code == 403

    token = await client.get("/csrf.json")
    assert token.status_code == 200
    csrf = (await token.get_json())["csrf_token"]
    assert csrf

    created = await client.post(
        "/autodj/clocks.json",
        json=CLOCK_BODY,
        headers={HEADER_NAME: csrf},
    )
    assert created.status_code == 201, await created.get_data(as_text=True)


async def test_htmx_mutation_requires_csrf_token(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    client = await _admin_client(
        _csrf_config(app_configration),
        beets_integration,
        dbsession,
        users_password,
        "csrf-htmx",
    )
    htmx = {"HX-Request": "true"}
    denied = await client.post(
        "/autodj",
        form={"title": "x", "color": "#aabbcc"},
        headers=htmx,
    )
    assert denied.status_code == 403

    token = await client.get("/csrf.json")
    csrf = (await token.get_json())["csrf_token"]
    accepted = await client.post(
        "/autodj",
        form={"title": "x", "color": "#aabbcc"},
        headers={**htmx, HEADER_NAME: csrf},
    )
    assert accepted.status_code != 403
