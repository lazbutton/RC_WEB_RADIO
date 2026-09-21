from __future__ import annotations

import json
from pathlib import Path


def test_export_writes_every_table_without_secrets(kernel, tmp_path, monkeypatch):
    from regie import __main__ as cli

    kernel.auth.create_user("laz@test", "Laz", "motdepasse-solide", "admin")
    kernel.secrets.put("google", "refresh:1", "ya29.secret")
    kernel.set_setting("mail.signature", "— Laz")
    monkeypatch.setenv("REGIE_DSN", kernel.dsn)
    from regie.config import get_settings

    get_settings.cache_clear()
    assert cli.main(["export", str(tmp_path)]) == 0
    export = next(tmp_path.glob("regie-export-*"))
    manifest = json.loads((export / "MANIFEST.json").read_text())
    assert manifest["tables"]["users"] == 1 and "secrets" not in manifest["tables"] and "sessions" not in manifest["tables"]
    users = json.loads((export / "users.json").read_text())
    assert users[0]["email"] == "laz@test" and "password_hash" not in users[0]
    assert (export / "fichiers-nas.txt").is_file()
    blob = b"".join(p.read_bytes() for p in export.glob("*.json"))
    assert b"ya29" not in blob and b"scrypt$" not in blob
    assert Path(export / "settings.json").is_file()
