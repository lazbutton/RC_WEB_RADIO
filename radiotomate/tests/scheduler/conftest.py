from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from quart.testing import (
    # avoid quart.typing.TestClientProtocol because pytest considers it as a test class
    QuartClient,
)
from sqlalchemy.orm import Session as ormSession

from radiotomate.beets.demo import BeetsMockIntegration
from radiotomate.commands import load_config
from radiotomate.enums import CartMode, ScheduleMode
from radiotomate.models import Cart, Sound, User
from radiotomate.quart import CustomQuart
from radiotomate.scheduler_app import app_factory


@pytest.fixture(scope="session")
def beets_integration():
    mock = BeetsMockIntegration()
    yield mock
    mock.teardown()


@pytest.fixture
def app_configration() -> Generator[dict, None, None]:
    """
    Create a configuration pointing to a DB file, both in a temporary folder.

    We cannot use ``:memory:?cache=shared`` because, as we are running simultaneous
    threads (APScheduler and app), it is too hard to know if everbody's finished and
    sometimes a dangling connection leaked the DB from one test to the next.
    """
    template_path = Path(__file__).parent / "tests.yaml"
    template = template_path.read_bytes()

    with TemporaryDirectory() as tmpdir:
        db_uri = f"sqlite+aiosqlite:///{tmpdir}/db.db"
        content = template.replace(b"db_url_goes_here", db_uri.encode())
        config_path = Path(tmpdir) / "radiotomate.yaml"
        config_path.write_bytes(content)
        config = load_config(config_path, verbose=True, main_handler="scheduler")
        yield config


@pytest.fixture
async def raw_app(app_configration: dict, beets_integration) -> CustomQuart:
    """
    Call this one directly only for tests that need to wait until background tasks
    are finished: those tests must make the ``test_app()`` and test client themselves.
    Be careful that the DB will not be usable out of that context block, because
    ending the test_app triggers ``after_serving``, who closes the engine.
    """
    app = app_factory(app_configration, beets_integration)
    engine = app.extensions["sqlalchemy"].engine
    from radiotomate.commands.update import do_update

    await do_update(engine)
    return app


@pytest.fixture
async def app(raw_app: CustomQuart) -> AsyncGenerator[CustomQuart]:
    async with raw_app.test_app() as test_app:  # ensure {before,while,after}_serving
        yield test_app.app


@pytest.fixture
def client(app: CustomQuart) -> QuartClient:
    return app.test_client()


@pytest.fixture
def auth() -> dict:
    # see tests.yaml
    token = "24e32490bca776cfeddb44945268818eb9e98e0bde69497dc792428ac15ba992"
    return {
        "X-Auth-Token": token,
    }


@pytest.fixture
async def dbsession(raw_app: CustomQuart) -> AsyncGenerator[ormSession]:
    async with raw_app.extensions["sqlalchemy"].session() as session:
        yield session


@pytest.fixture
async def fake_cart(
    raw_app: CustomQuart,
    dbsession: ormSession,
) -> Cart:
    cartpath = raw_app.config["DATA_ROOT"] / "radiotomate-test-cart"
    cart = Cart(
        title="Testing Radiotomate",
        path=cartpath,
        mode=CartMode.PLAYLIST,
        schedule_mode=ScheduleMode.TIMED,
    )
    dbsession.add(cart)
    await dbsession.commit()
    return cart


@pytest.fixture
async def fake_sound(dbsession: ormSession, fake_cart: Cart) -> Sound:
    soundpath = fake_cart.path / "radiotomate-test-sound.mp3"
    sound = Sound(
        cart_id=fake_cart.id,
        path=soundpath,
        duration=10,
        title="Test sound",
        gain=-2.5,
        peak=-0.5,
    )
    dbsession.add(sound)
    await dbsession.commit()
    return sound


@pytest.fixture
async def fake_sound2(dbsession: ormSession, fake_cart: Cart) -> Sound:
    path = fake_cart.path / "radiotomate-other-test-sound.mp3"
    sound = Sound(cart_id=fake_cart.id, path=path, duration=20, title="Testers' blues")
    dbsession.add(sound)
    await dbsession.commit()
    return sound


@pytest.fixture
def users_password() -> str:
    return "lostpassword"


@pytest.fixture
async def user_no_permission(dbsession: ormSession, users_password: str) -> User:
    """
    Returns an user with no permissions
    """
    user = User(username="unknown")
    user.update_password(users_password)
    dbsession.add(user)
    await dbsession.commit()
    return user


@pytest.fixture
async def user_can_stream(dbsession: ormSession, users_password: str) -> User:
    """
    Returns an user with live streaming authorization
    """
    user = User(username="liveDJ")
    user.update_password(users_password)
    user.update_permissions({"can_stream": "true"})
    dbsession.add(user)
    await dbsession.commit()
    return user
