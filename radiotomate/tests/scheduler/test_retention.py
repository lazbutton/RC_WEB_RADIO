import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session as ormSession

from radiotomate.enums import CommandStatus, RundownStatus
from radiotomate.models import MetadataLog
from radiotomate.models.execution import (
    PlayoutCommand,
    ProgrammingVersion,
    RundownItem,
)
from radiotomate.quart import CustomQuart
from radiotomate.scheduler import retention


def _item(item_id: str, version: str, *, at: datetime, status: str) -> RundownItem:
    return RundownItem(
        id=item_id,
        programming_version_id=version,
        sequence=0,
        planned_at=at,
        duration=180,
        kind="musique",
        when_mode="sequential",
        queue="autodj",
        resource="x",
        status=status,
    )


def _command(cmd_id: str, item_id: str | None, *, at: datetime, status: str):
    return PlayoutCommand(
        id=cmd_id,
        rundown_item_id=item_id,
        action="queue",
        queue="autodj",
        payload={},
        idempotency_key=cmd_id,
        status=status,
        created=at,
        available_at=at,
    )


async def _count(session, model) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)))


async def test_retention_purges_closed_history_only(
    raw_app: CustomQuart, dbsession: ormSession
):
    now = datetime.now()
    old = now - timedelta(days=10)
    dbsession.add_all(
        [
            ProgrammingVersion(id="pv-live", source="t", description="live"),
            ProgrammingVersion(
                id="pv-orphan", source="t", description="orphan", created=old
            ),
            ProgrammingVersion(
                id="pv-fresh-orphan", source="t", description="fresh", created=now
            ),
        ]
    )
    await dbsession.flush()
    dbsession.add_all(
        [
            _item(
                "ri-old-played", "pv-live", at=old, status=RundownStatus.PLAYED.value
            ),
            _item(
                "ri-old-planned", "pv-live", at=old, status=RundownStatus.PLANNED.value
            ),
            _item(
                "ri-new-played",
                "pv-live",
                at=now - timedelta(hours=1),
                status=RundownStatus.PLAYED.value,
            ),
        ]
    )
    dbsession.add_all(
        [
            _command(
                "cmd-old-ack",
                "ri-old-played",
                at=now - timedelta(days=2),
                status=CommandStatus.ACKNOWLEDGED.value,
            ),
            _command(
                "cmd-old-pending",
                None,
                at=now - timedelta(days=2),
                status=CommandStatus.PENDING.value,
            ),
            _command(
                "cmd-new-ack",
                "ri-new-played",
                at=now - timedelta(hours=1),
                status=CommandStatus.ACKNOWLEDGED.value,
            ),
        ]
    )
    dbsession.add_all(
        [
            MetadataLog(
                on_air=now - timedelta(days=120),
                source="autodj",
                artist="Vieux",
                title="Titre",
                rundown_item_id="ri-old-played",
                playout_command_id="cmd-old-ack",
                extra={"genre": "x"},
            ),
            MetadataLog(
                on_air=now - timedelta(days=1),
                source="carts",
                artist="Récent",
                title="Titre",
            ),
        ]
    )
    await dbsession.commit()

    result = await retention.run_retention(raw_app, now=now)

    assert result == {
        "commands": 1,
        "rundown_items": 1,
        "programming_versions": 1,
        "metadata_archived": 1,
    }
    remaining_items = set(await dbsession.scalars(select(RundownItem.id)))
    assert remaining_items == {"ri-old-planned", "ri-new-played"}
    remaining_cmds = set(await dbsession.scalars(select(PlayoutCommand.id)))
    assert remaining_cmds == {"cmd-old-pending", "cmd-new-ack"}
    remaining_versions = set(await dbsession.scalars(select(ProgrammingVersion.id)))
    assert remaining_versions == {"pv-live", "pv-fresh-orphan"}
    assert await _count(dbsession, MetadataLog) == 1

    export_dir = Path(raw_app.config["DATA_ROOT"]) / retention.EXPORT_DIR
    files = sorted(export_dir.glob("metadata_log-*.jsonl"))
    assert len(files) == 1
    row = json.loads(files[0].read_text().splitlines()[0])
    assert row["artist"] == "Vieux"
    assert row["rundown_item_id"] == "ri-old-played"


async def test_retention_settings_defaults():
    conf = retention.settings_from_config({"RETENTION_INTERVAL": "abc"})
    assert conf["enabled"] is True
    assert conf["interval"] == retention.DEFAULT_INTERVAL_SECONDS
    assert conf["rundown_days"] == retention.DEFAULT_RUNDOWN_DAYS
    conf = retention.settings_from_config({"RETENTION_INTERVAL": 10})
    assert conf["interval"] == retention.MIN_INTERVAL_SECONDS
