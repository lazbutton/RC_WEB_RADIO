from quart.testing import QuartClient


async def test_version(client: QuartClient):
    result = await client.get("/version")
    result_text = (await result.data).decode()
    assert result.status_code == 200, "got non-OK response:" + result_text
    assert await result.json == {
        "radiotomate": "0.1.0",
        "liquidsoap": "disconnected",
    }
