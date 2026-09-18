from sqlalchemy.orm import Session as ormSession

from radiotomate.beets import BeetsIntegration
from radiotomate.models import User

JSON_PATHS = (
    "/autodj/conducteur.json",
    "/autodj/clocks.json",
    "/autodj/slots.json",
    "/autodj/categories.json",
    "/carts.json",
)


def _interface_client(app_configration: dict, beets_integration: BeetsIntegration):
    from radiotomate.interface_app import app_factory as interface_factory

    iface = interface_factory(app_configration, False, beets_integration)
    iface.config["INTERFACE_NAME"] = "New Trad Radio"
    return iface.test_client()


async def _login(client, username: str, password: str):
    login = await client.post(
        "/login",
        form={"username": username, "password": password},
    )
    assert login.status_code == 302


async def test_json_unauthenticated_returns_401(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
):
    client = _interface_client(app_configration, beets_integration)
    for path in JSON_PATHS:
        response = await client.get(path)
        assert response.status_code == 401, path
        payload = await response.get_json()
        assert payload["error"] == "unauthenticated"


async def test_html_autodj_still_redirects_to_login(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
):
    client = _interface_client(app_configration, beets_integration)
    response = await client.get("/autodj")
    assert response.status_code == 302
    assert "/login" in (response.headers.get("Location") or "")


async def test_studio_json_authenticated(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    jingles_ntr_cart,
    pubs_cart,
):
    user = User(username="studio-json")
    user.update_password(users_password)
    dbsession.add(user)
    await dbsession.commit()

    client = _interface_client(app_configration, beets_integration)
    await _login(client, "studio-json", users_password)

    clocks = await (await client.get("/autodj/clocks.json")).get_json()
    names = {c["name"] for c in clocks["clocks"]}
    assert names == {"24/24 Rotation habillée", "Journée pubs"}
    journee = next(c for c in clocks["clocks"] if c["name"] == "Journée pubs")
    assert [a["minute"] for a in journee["anchors"]] == [20, 40]
    assert journee["motif"][0]["kind"] == "jingle"
    rotation24 = next(
        c for c in clocks["clocks"] if c["name"] == "24/24 Rotation habillée"
    )
    assert rotation24["anchors"] == []
    assert len(rotation24["motif"]) == 4

    slots = await (await client.get("/autodj/slots.json")).get_json()
    minutes = {s["minute"] for s in slots["slots"]}
    assert minutes == {0, 10 * 60, 18 * 60}
    assert len(slots["slots"]) == 21
    at_10 = next(s for s in slots["slots"] if s["minute"] == 10 * 60)
    assert at_10["clock"] == "Journée pubs"
    at_18 = next(s for s in slots["slots"] if s["minute"] == 18 * 60)
    assert at_18["clock"] == "24/24 Rotation habillée"

    categories = await (await client.get("/autodj/categories.json")).get_json()
    rotation = next(c for c in categories["categories"] if c["name"] == "Rotation")
    assert rotation["query"] == "grouping:rotation"
    assert rotation["count"] > 0

    carts = await (await client.get("/carts.json")).get_json()
    titles = {c["title"] for c in carts["carts"]}
    assert "Jingles NTR" in titles
    assert "Pubs" in titles
    jingles = next(c for c in carts["carts"] if c["title"] == "Jingles NTR")
    assert jingles["sounds"]
    assert "path" not in jingles
    assert "path" not in jingles["sounds"][0]
