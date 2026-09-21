from pathlib import Path

import pytest
from sqlalchemy.orm import Session as ormSession

from radiotomate.beets import BeetsIntegration
from radiotomate.models import Cart, Sound, User
from radiotomate.services.carts import (
    forget_legacy_files,
    reconcile_cart_bank,
    sync_bank_folder,
)
from tests.scheduler.test_admin_json import ASSETS, _admin_client, _mp3_upload


async def test_finder_carts_roundtrip(  # noqa: PLR0913, PLR0915
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    media = tmp_path / "media"
    jingles = media / "30-habillage" / "jingles"
    jingles.mkdir(parents=True)
    mp3 = ASSETS / "ohradiotomateoh.mp3"
    (jingles / "ouverture.mp3").write_bytes(mp3.read_bytes())
    monkeypatch.setenv("MEDIA_ROOT", str(media))

    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "finder-carts",
    )

    created = await client.post(
        "/carts.json",
        json={"title": "Prog", "mode": "playlist", "schedule_mode": "timed"},
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    prog = (await created.get_json())["cart"]
    prog_id = prog["id"]
    assert prog["bank_folder"] == f"50-carts/{prog_id}"
    assert prog["finder_path"] == f"Carts/{prog_id}-Prog"
    real = media / "50-carts" / str(prog_id)
    view = media / "Carts" / f"{prog_id}-Prog"
    assert real.is_dir()
    assert view.is_symlink()
    assert view.resolve() == real.resolve()

    uploaded = await client.post(
        f"/carts/{prog_id}/sounds.json",
        files={"sounds": _mp3_upload(mp3)},
    )
    assert uploaded.status_code == 201, await uploaded.get_data(as_text=True)
    sounds = (await uploaded.get_json())["cart"]["sounds"]
    assert len(sounds) == 1
    assert sounds[0]["path"] == f"50-carts/{prog_id}/ohradiotomateoh.mp3"
    assert "Carts/" not in sounds[0]["path"]
    assert (real / "ohradiotomateoh.mp3").is_file()
    assert (view / "ohradiotomateoh.mp3").is_file()

    dropped = view / "drop-finder.mp3"
    dropped.write_bytes(mp3.read_bytes())
    dbsession.expire_all()
    cart = await Cart.from_id(dbsession, prog_id, load_sounds=True)
    user = await User.from_username(dbsession, "finder-carts")
    assert cart is not None
    assert user is not None
    uploader_id = user.id
    added = await sync_bank_folder(dbsession, cart, uploader_id)
    await dbsession.commit()
    assert [sound.title for sound in added] == ["drop-finder.mp3"]
    dbsession.expire_all()
    cart = await Cart.from_id(dbsession, prog_id, load_sounds=True)
    assert cart is not None
    paths = [str(sound.path) for sound in cart.sounds]
    assert any(path.endswith(f"50-carts/{prog_id}/drop-finder.mp3") for path in paths)
    assert not any("Carts/" in path for path in paths)

    dropped.unlink()
    dbsession.expire_all()
    cart = await Cart.from_id(dbsession, prog_id, load_sounds=True)
    pruned = await sync_bank_folder(dbsession, cart, uploader_id)
    await dbsession.commit()
    assert pruned == []
    dbsession.expire_all()
    cart = await Cart.from_id(dbsession, prog_id, load_sounds=True)
    assert cart is not None
    titles = [sound.title for sound in cart.sounds]
    assert "drop-finder.mp3" not in titles
    assert "ohradiotomateoh.mp3" in titles

    keep_id = next(
        sound.id for sound in cart.sounds if sound.title == "ohradiotomateoh.mp3"
    )
    gone = await client.delete(f"/carts/{prog_id}/sounds/{keep_id}.json")
    assert gone.status_code == 200
    assert not (real / "ohradiotomateoh.mp3").is_file()
    trash = list((media / "90-trash").glob("ohradiotomateoh*.mp3"))
    assert trash, "exclusive cart file should move to 90-trash"

    renamed = await client.put(
        f"/carts/{prog_id}.json",
        json={"title": "Prog Prime"},
    )
    assert renamed.status_code == 200, await renamed.get_data(as_text=True)
    body = await renamed.get_json()
    assert body["cart"]["bank_folder"] == f"50-carts/{prog_id}"
    assert body["cart"]["finder_path"] == f"Carts/{prog_id}-Prog-Prime"
    assert not (media / "Carts" / f"{prog_id}-Prog").exists()
    assert (media / "Carts" / f"{prog_id}-Prog-Prime").is_symlink()
    assert (media / "50-carts" / str(prog_id)).is_dir()

    jingled = await client.post(
        "/carts.json",
        json={"title": "jingles", "mode": "playlist", "schedule_mode": "timed"},
    )
    assert jingled.status_code == 201, await jingled.get_data(as_text=True)
    jingle_cart = (await jingled.get_json())["cart"]
    jingle_id = jingle_cart["id"]
    assert jingle_cart["bank_folder"] == "30-habillage/jingles"
    jview = media / "Carts" / f"{jingle_id}-jingles"
    assert jview.is_symlink()
    assert jview.resolve() == jingles.resolve()

    dbsession.expire_all()
    cart = await Cart.from_id(dbsession, jingle_id, load_sounds=True)
    added_j = await sync_bank_folder(dbsession, cart, uploader_id)
    await dbsession.commit()
    assert [sound.title for sound in added_j] == ["ouverture.mp3"]
    dbsession.expire_all()
    cart = await Cart.from_id(dbsession, jingle_id, load_sounds=True)
    assert cart is not None
    stored = str(cart.sounds[0].path)
    assert stored.endswith("30-habillage/jingles/ouverture.mp3")
    assert "Carts/" not in stored

    other = await client.post(
        "/carts.json",
        json={"title": "Mix", "mode": "playlist", "schedule_mode": "timed"},
    )
    mix_id = (await other.get_json())["cart"]["id"]
    attached = await client.post(
        f"/carts/{mix_id}/sounds.json",
        json={"paths": ["30-habillage/jingles/ouverture.mp3"]},
    )
    assert attached.status_code == 201, await attached.get_data(as_text=True)
    mix_sound = (await attached.get_json())["cart"]["sounds"][0]["id"]
    gone_mix = await client.delete(f"/carts/{mix_id}/sounds/{mix_sound}.json")
    assert gone_mix.status_code == 200
    assert (jingles / "ouverture.mp3").is_file()

    finder_put = await client.put(
        f"/carts/{mix_id}.json",
        json={"bank_folder": f"Carts/{mix_id}-Mix"},
    )
    assert finder_put.status_code == 400

    rotation = await client.put(
        f"/carts/{mix_id}.json",
        json={"bank_folder": "10-rotation"},
    )
    assert rotation.status_code == 400

    data_root = Path(app_configration["data"]["root"])
    legacy = data_root / "carts" / f"{mix_id}-legacy.mp3"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(mp3.read_bytes())
    dbsession.expire_all()
    mix = await Cart.from_id(dbsession, mix_id)
    assert mix is not None
    sound = Sound(
        cart_id=mix_id,
        rank=1,
        title="legacy.mp3",
        path=legacy,
        duration=1,
        uploader_id=uploader_id,
    )
    dbsession.add(sound)
    await dbsession.commit()
    dbsession.expire_all()
    mix = await Cart.from_id(dbsession, mix_id)
    _, stale = await reconcile_cart_bank(dbsession, mix, uploader_id)
    await dbsession.commit()
    forget_legacy_files(stale)
    dbsession.expire_all()
    mix = await Cart.from_id(dbsession, mix_id, load_sounds=True)
    assert mix is not None
    assert mix.bank_folder == f"50-carts/{mix_id}"
    migrated = next(s for s in mix.sounds if s.title == "legacy.mp3")
    assert "50-carts" in str(migrated.path)
    assert not legacy.is_file()
