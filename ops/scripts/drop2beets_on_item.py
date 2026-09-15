"""
on_item drop2beets — coller dans Beets/config.yaml sous drop2beets.on_item.

Les sous-dossiers MusicDropbox/{ntf1,ntf2,ntf3,rotation} posent grouping=
pour les filtres auto-DJ. habits/ est ignoré ici (carts jingles).
"""


def on_item(item, path):
    if not (item.artist or "").strip() or not (item.title or "").strip():
        return None
    folder = (path or "").strip("/").split("/")[0] if path else ""
    if folder in {"ntf1", "ntf2", "ntf3"}:
        item.grouping = folder
        item.genre = item.genre or "Archive"
    elif folder == "rotation":
        item.grouping = "rotation"
    elif folder == "habits":
        return None
    return item
