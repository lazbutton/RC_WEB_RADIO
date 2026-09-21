from pathlib import Path

import pytest

from radiotomate.domain.errors import DomainValidationError
from radiotomate.services import media_bank as bank

ASSETS = Path(__file__).resolve().parent / "assets"


@pytest.fixture
def media(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "media"
    (root / "30-habillage" / "jingles").mkdir(parents=True)
    monkeypatch.setenv("MEDIA_ROOT", str(root))
    return root


def test_canonical_jingles_and_prog(media: Path):
    assert bank.canonical_rel_for_cart(3, "Jingles") == "30-habillage/jingles"
    assert bank.canonical_rel_for_cart(8, "Prog") == "50-carts/8"
    assert bank.finder_rel(8, "Prog") == "Carts/8-Prog"


def test_ensure_tree_symlink_is_view_only(media: Path):
    rel = bank.ensure_cart_tree(3, "Jingles")
    assert rel == "30-habillage/jingles"
    real = (media / rel).resolve()
    view = media / "Carts" / "3-Jingles"
    assert view.is_symlink()
    assert view.resolve() == real


def test_prog_tree_is_real_dir(media: Path):
    rel = bank.ensure_cart_tree(8, "Prog")
    assert rel == "50-carts/8"
    assert (media / rel).is_dir()
    view = media / "Carts" / "8-Prog"
    assert view.is_symlink()
    assert view.resolve() == (media / rel).resolve()


def test_rename_refreshes_finder_slug_keeps_id_dir(media: Path):
    bank.ensure_cart_tree(8, "Prog")
    rel = bank.ensure_cart_tree(8, "Prog Prime", "50-carts/8")
    assert rel == "50-carts/8"
    assert not (media / "Carts" / "8-Prog").exists()
    assert (media / "Carts" / "8-Prog-Prime").is_symlink()


def test_reject_rotation_inbox_and_finder_view(media: Path):
    (media / "10-rotation").mkdir()
    (media / "00-inbox" / "x").mkdir(parents=True)
    (media / "Carts" / "x").mkdir(parents=True)
    with pytest.raises(DomainValidationError):
        bank.resolve_bank_dir("10-rotation")
    with pytest.raises(DomainValidationError):
        bank.resolve_bank_dir("00-inbox/x")
    with pytest.raises(DomainValidationError):
        bank.resolve_bank_dir("Carts/x")
    rel = bank.ensure_cart_tree(1, "X")
    assert rel == "50-carts/1"
    bank.resolve_bank_dir(rel)


def test_iter_skips_symlink_escape(media: Path):
    dest = media / "50-carts" / "1"
    dest.mkdir(parents=True)
    outside = media / "10-rotation"
    outside.mkdir(exist_ok=True)
    mp3 = ASSETS / "ohradiotomateoh.mp3"
    target = outside / "secret.mp3"
    target.write_bytes(mp3.read_bytes())
    (dest / "trap.mp3").symlink_to(target)
    (dest / "ok.mp3").write_bytes(mp3.read_bytes())
    files = {path.name for path in bank.iter_bank_audio(dest)}
    assert files == {"ok.mp3"}
