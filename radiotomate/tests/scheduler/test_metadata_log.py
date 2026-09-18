from datetime import datetime
from unittest.mock import patch

import httpx
from sqlalchemy.orm import Session as ormSession

from radiotomate.models import MetadataLog, Sound
from radiotomate.quart import CustomQuart
from radiotomate.scheduler.metrics import runtime_metrics


async def test_metadata_log(
    raw_app: CustomQuart,
    dbsession: ormSession,
    fake_sound: Sound,
    auth: dict,
):
    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_relay:
        async with raw_app.test_app():
            client = raw_app.test_client()

            result = await client.post(
                "/metadata_log",
                json={"source": "unittests"},
                headers=auth,
            )
            result_text = (await result.data).decode()
            assert result.status_code == 200, "got non-OK response:" + result_text

            logged = await MetadataLog.get(dbsession, limit=1)
            assert logged[0].source == "unittests"

            complete_md = {
                "source": "complete_md",
                "radiotomate_sound_id": fake_sound.id,
                "on_air": datetime.now().isoformat(),
                "artist": "Test artist",
                "title": "Test title",
                "album": "Test album",
                "more": "more metadata",
                "even_more": "even more metadata",
                "initial_uri": "http://radiotomate.test/test.mp3",
            }
            result = await client.post("/metadata_log", json=complete_md, headers=auth)
            result_text = (await result.data).decode()
            assert result.status_code == 200, "got non-OK response:" + result_text

            logged = await MetadataLog.get(dbsession, limit=2)
            assert logged[0].source == complete_md["source"]
            assert logged[0].artist == complete_md["artist"]
            assert logged[0].title == complete_md["title"]
            assert logged[0].album == complete_md["album"]
            assert logged[0].on_air == datetime.fromisoformat(complete_md["on_air"])
            assert logged[0].source_url == complete_md["initial_uri"]
            assert logged[0].extra["more"] == complete_md["more"]
            assert logged[0].extra["even_more"] == complete_md["even_more"]

            await dbsession.refresh(fake_sound)
            assert fake_sound.last_played == logged[0].on_air

        mock_relay.assert_not_called()


async def test_metadata_log_relay(
    raw_app: CustomQuart,
    fake_sound: Sound,
    auth: dict,
):
    additional_fields = {
        "SECRET_KEY": "secret_value",
        "SOURCE_NAME": "radiotomate",
    }
    raw_app.config["RELAY_METADATA_TO"] = [
        {
            "url": "https://website.radio/playlist/add_item",
            "add_field": additional_fields,
        }
    ]

    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_relay:
        async with raw_app.test_app():
            client = raw_app.test_client()

            complete_md = {
                "source": "complete_md",
                "radiotomate_sound_id": fake_sound.id,
                "on_air": datetime.now().isoformat(),
                "artist": "Test artist",
                "title": "Test title",
                "album": "Test album",
                "more": "more metadata",
                "even_more": "even more metadata",
                "initial_uri": "http://radiotomate.test/test.mp3",
            }
            result = await client.post("/metadata_log", json=complete_md, headers=auth)
            result_text = (await result.data).decode()
            assert result.status_code == 200, "got non-OK response:" + result_text

        complete_md.update(additional_fields)
        mock_relay.assert_called_once_with(
            "https://website.radio/playlist/add_item",
            data=complete_md,
            headers=None,
        )


async def test_metadata_log_relay_headers(
    raw_app: CustomQuart,
    fake_sound: Sound,
    auth: dict,
):
    additional_headers = {
        "Authorization": "Basic YWxhZGRpbjpvcGVuc2VzYW1l",
    }
    raw_app.config["RELAY_METADATA_TO"] = [
        {
            "url": "https://myradio.org/track_recorder.php",
            "add_header": additional_headers,
        }
    ]

    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_relay:
        async with raw_app.test_app():
            client = raw_app.test_client()

            complete_md = {
                "source": "complete_md",
                "radiotomate_sound_id": fake_sound.id,
                "on_air": datetime.now().isoformat(),
                "artist": "Test artist",
                "title": "Test title",
                "album": "Test album",
                "more": "more metadata",
                "even_more": "even more metadata",
                "initial_uri": "http://radiotomate.test/test.mp3",
            }
            result = await client.post("/metadata_log", json=complete_md, headers=auth)
            result_text = (await result.data).decode()
            assert result.status_code == 200, "got non-OK response:" + result_text

        mock_relay.assert_called_once_with(
            "https://myradio.org/track_recorder.php",
            data=complete_md,
            headers=additional_headers,
        )


async def test_metadata_log_retries_transient_failure(
    raw_app: CustomQuart,
    auth: dict,
):
    raw_app.config["RELAY_METADATA_TO"] = [{"url": "https://website.radio/hook"}]
    raw_app.config["RELAY_METADATA_RETRY"] = {
        "max_attempts": 3,
        "timeout_seconds": 1,
        "backoff_seconds": [0, 0],
    }

    with patch(
        "httpx.AsyncClient.post",
        side_effect=[
            httpx.ConnectError("temporarily down"),
            httpx.Response(503),
            httpx.Response(204),
        ],
    ) as mock_relay:
        async with raw_app.test_app():
            result = await raw_app.test_client().post(
                "/metadata_log",
                json={"source": "retry-test"},
                headers=auth,
            )
            assert result.status_code == 200

    assert mock_relay.call_count == 3
    assert runtime_metrics.metadata_relay_total == {
        "retry": 2,
        "success": 1,
    }


async def test_metadata_log_does_not_retry_client_error(
    raw_app: CustomQuart,
    auth: dict,
):
    raw_app.config["RELAY_METADATA_TO"] = [{"url": "https://website.radio/hook"}]

    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(400),
    ) as mock_relay:
        async with raw_app.test_app():
            result = await raw_app.test_client().post(
                "/metadata_log",
                json={"source": "client-error"},
                headers=auth,
            )
            assert result.status_code == 200

    mock_relay.assert_called_once()
    assert runtime_metrics.metadata_relay_total == {"failure": 1}
