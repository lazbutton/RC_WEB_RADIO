from pathlib import Path

from radiotomate.beets.demo import seed_items_from_root, split_sound_title


def test_split_sound_title_strips_index_and_artist():
    assert split_sound_title("01 Genevieve Murphy - About To Turn 8") == (
        "Genevieve Murphy",
        "About To Turn 8",
    )
    assert split_sound_title("Tanika Charles - 08 Different Morning") == (
        "Tanika Charles",
        "Different Morning",
    )


def test_seed_items_from_ntr_radio_data():
    root = Path(__file__).resolve().parents[2] / "radio_data"
    if not (root / "radiotomate.db").is_file():
        return
    items = seed_items_from_root(root)
    if not items:
        return
    assert items
    assert all(item.title for item in items)
    assert all(Path(item.path.decode()).is_file() for item in items)
    joined = " ".join(f"{item.artist} {item.title}" for item in items)
    assert "radioart" not in joined
    assert "cast toward" not in joined.lower()
