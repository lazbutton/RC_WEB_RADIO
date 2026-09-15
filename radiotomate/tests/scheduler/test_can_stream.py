from quart.testing import QuartClient

from radiotomate.models import User


async def test_can_stream(
    client: QuartClient,
    users_password: str,
    user_can_stream: User,
    auth: dict,
):
    result = await client.post(
        "/can_stream",
        json={
            # this must match the object passed by liquidsoap to input.harbor's "auth"
            "user": user_can_stream.username,
            "password": users_password,
        },
        headers=auth,
    )
    result_text = (await result.data).decode()
    assert result.status_code == 200, "got non-OK response:" + result_text


async def test_cannot_stream(
    client: QuartClient,
    users_password: str,
    user_can_stream: User,
    user_no_permission: User,
    auth: dict,
):
    result = await client.post(
        "/can_stream",
        json={
            "user": user_can_stream.username,
            "password": users_password + "nope",
        },
        headers=auth,
    )
    result_text = (await result.data).decode()
    assert result.status_code != 200, "got OK on a wrong password:" + result_text

    result = await client.post(
        "/can_stream",
        json={
            "user": user_no_permission.username,
            "password": users_password,
        },
        headers=auth,
    )
    result_text = (await result.data).decode()
    assert result.status_code != 200, (
        "got OK for a user that has no permissions" + result_text
    )
