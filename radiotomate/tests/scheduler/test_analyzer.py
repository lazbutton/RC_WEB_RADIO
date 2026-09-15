from asyncio import sleep

from quart.testing import QuartClient
from sqlalchemy.orm import Session as ormSession

from radiotomate.enums import ScheduleMode
from radiotomate.models import Cart, Sound
from tests.scheduler import schedule_in_two_seconds


async def test_post_analyzer(  # noqa: PLR0913
    client: QuartClient,
    dbsession: ormSession,
    fake_cart: Cart,
    fake_sound: Sound,
    fake_sound2: Sound,
    auth: dict,
):
    fake_cart.schedule_mode = ScheduleMode.TIMED
    schedule_in_two_seconds(fake_cart)
    fake_sound.gain = None
    fake_sound.peak = None
    fake_sound2.gain = None
    fake_sound2.peak = None
    await dbsession.commit()

    result = await client.post(
        "/analyzer",
        headers=auth,
        json={
            "sound_ids": [fake_sound.id, fake_sound2.id],
        },
    )
    result_text = (await result.data).decode()
    assert result.status_code == 200, "got non-OK response:" + result_text

    # just a slight delay so the background task can update fields
    await sleep(0.1)
    await dbsession.refresh(fake_sound)
    assert fake_sound.gain is not None
    assert fake_sound.peak is not None
    await dbsession.refresh(fake_sound2)
    assert fake_sound2.gain is not None
    assert fake_sound2.peak is not None
