"""Saved game backup & restore (#179).

The archive is built by the manager itself, so these tests exercise the real
thing. The restore runs in a sibling container (root-owned files); that is acted
out here on the paths the service asks for, exactly as the clear-data tests do.
"""
import io
import json
import tarfile

import pytest
from sqlmodel import Session

from models import Instance, Template, get_engine
from services import instance_backup, instance_service


def _seed(tmp_path, monkeypatch, *, template_config=None):
    """An instance with a Freedom-Fighters-shaped profile: a save, mod data, logs."""
    import config

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    with Session(get_engine()) as session:
        template = Template(
            name="Antons Testserver",
            config_json=template_config
            or json.dumps({
                "game": {
                    "scenarioId": "{59AD59368755F41A}Missions/21_GM_Eden.conf",
                    "gameProperties": {"persistence": {"hiveId": 0}},
                }
            }),
        )
        session.add(template)
        session.commit()
        session.refresh(template)
        inst = Instance(
            name="ff", template_id=template.id, branch="stable",
            game_port=2001, a2s_port=17777, rcon_port=19999,
        )
        session.add(inst)
        session.commit()
        session.refresh(inst)
        instance_id = inst.id

    idir = tmp_path / "instances" / str(instance_id)
    profile = idir / "profile" / "profile"
    (profile / ".save" / "game").mkdir(parents=True)
    (profile / ".save" / "game" / "save.bin").write_bytes(b"world" * 20)
    (profile / "FFShopPricing").mkdir()
    (profile / "FFShopPricing" / "prices.json").write_text('{"ak": 100}')
    (profile / ".db" / "store").mkdir(parents=True)
    (profile / ".db" / "store" / "rows.db").write_bytes(b"db")
    (profile / "FreedomFighters_ServerConfig.json").write_text("{}")
    (profile / "ownerToken.bin").write_bytes(b"secret-identity")
    (idir / "profile" / "logs" / "session1").mkdir(parents=True)
    (idir / "profile" / "logs" / "session1" / "console.log").write_text("noise" * 100)
    (idir / "profile" / "addons").mkdir()
    (idir / "profile" / "addons" / "baked.pak").write_bytes(b"x" * 500)
    (idir / "workshop").mkdir()
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: False)
    monkeypatch.setattr(instance_service, "container_status", lambda _id: "exited")
    return instance_id, idir


def _fake_container_run(monkeypatch, idir):
    """Act out the restore container: run its shell script against the real dirs."""
    import shutil

    seen = {}

    class FakeContainers:
        def run(self, image, entrypoint=None, command=None, **kw):
            script = command[1]
            seen["script"] = script
            for statement in script.split(";"):
                statement = statement.strip()
                if statement.startswith("rm -rf "):
                    rel = statement.split("'")[1].replace("/idata/", "")
                    target = idir / rel
                    if target.is_dir():
                        shutil.rmtree(target, ignore_errors=True)
                    else:
                        target.unlink(missing_ok=True)
                elif statement.startswith("tar xzf "):
                    archive = idir / statement.split("'")[1].replace("/idata/", "")
                    with tarfile.open(archive, "r:gz") as tar:
                        tar.extractall(idir, filter="data")

    monkeypatch.setattr(
        instance_service.docker_service, "get_client",
        lambda: type("C", (), {"containers": FakeContainers()})(),
    )
    monkeypatch.setattr(instance_service.docker_service, "host_path_for", lambda p: p)
    return seen


# --------------------------------------------------------------------------- #
# What counts as game state
# --------------------------------------------------------------------------- #

def test_the_save_is_not_the_only_thing_a_scenario_writes(tmp_path, monkeypatch):
    """#179: mod state next to the save is state too, and used to be invisible."""
    instance_id, _ = _seed(tmp_path, monkeypatch)
    paths = instance_backup.state_summary(instance_id)["paths"]

    assert "profile/profile/.save" in paths
    assert "profile/profile/FFShopPricing" in paths          # the shop's prices
    assert "profile/profile/.db" in paths                    # the mod database
    assert "profile/profile/FreedomFighters_ServerConfig.json" in paths
    # ...but never the log pile, the mod bake, or the server's identity token
    assert not any("logs" in p or "addons" in p for p in paths)
    assert not any("ownerToken" in p for p in paths)


def test_state_is_reported_as_whole_subtrees_not_every_file(tmp_path, monkeypatch):
    instance_id, _ = _seed(tmp_path, monkeypatch)
    paths = instance_backup.state_summary(instance_id)["paths"]
    # .save holds a nested tree and nothing excluded, so it is one path, not three
    assert "profile/profile/.save/game" not in paths
    assert instance_backup.state_summary(instance_id)["files"] == 4


def test_the_stored_data_card_counts_the_same_thing(tmp_path, monkeypatch):
    """The saves row and a backup are one definition — they cannot drift (#179)."""
    instance_id, _ = _seed(tmp_path, monkeypatch)
    rows = {i["target"]: i for i in instance_service.instance_data(instance_id)["items"]}
    assert rows["saves"]["paths"] == instance_backup.state_summary(instance_id)["paths"]
    assert rows["logs"]["files"] == 1
    assert rows["mods"]["files"] == 1  # the bake, counted once, under mods only


# --------------------------------------------------------------------------- #
# Creating
# --------------------------------------------------------------------------- #

def test_a_backup_holds_the_state_and_says_where_it_came_from(tmp_path, monkeypatch):
    instance_id, idir = _seed(tmp_path, monkeypatch)
    meta = instance_backup.create_backup(instance_id, "before the swap")

    archive = idir / "backups" / f"{meta['id']}.tar.gz"
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    assert "profile/profile/.save/game/save.bin" in names
    assert "profile/profile/FFShopPricing/prices.json" in names
    assert not any("ownerToken" in n for n in names)
    assert not any("/logs/" in n or "/addons/" in n for n in names)

    assert meta["label"] == "before the swap"
    assert meta["template_name"] == "Antons Testserver"
    assert meta["scenario_id"].endswith("21_GM_Eden.conf")
    assert meta["files"] == 4 and meta["skipped"] == []


def test_the_archive_describes_itself_for_a_host_that_no_longer_exists(tmp_path, monkeypatch):
    instance_id, idir = _seed(tmp_path, monkeypatch)
    meta = instance_backup.create_backup(instance_id)
    with tarfile.open(idir / "backups" / f"{meta['id']}.tar.gz", "r:gz") as tar:
        embedded = json.loads(tar.extractfile(instance_backup.EMBEDDED_META).read())
    assert embedded["template_name"] == "Antons Testserver"
    assert embedded["instance_name"] == "ff"


def test_backing_up_nothing_is_refused_rather_than_producing_an_empty_file(tmp_path, monkeypatch):
    instance_id, idir = _seed(tmp_path, monkeypatch)
    import shutil

    shutil.rmtree(idir / "profile" / "profile")
    with pytest.raises(instance_service.InstanceError, match="no saved game data"):
        instance_backup.create_backup(instance_id)


def test_the_shelf_keeps_only_the_newest_ten(tmp_path, monkeypatch):
    instance_id, _ = _seed(tmp_path, monkeypatch)
    made = []
    for n in range(12):
        # _mint_id is per-second; force distinct ids without sleeping 12 seconds
        monkeypatch.setattr(
            instance_backup, "_mint_id", lambda _d, n=n: f"20260101-0000{n:02d}"
        )
        made.append(instance_backup.create_backup(instance_id)["id"])

    kept = [b["id"] for b in instance_backup.list_backups(instance_id)]
    assert len(kept) == instance_backup.KEEP
    assert made[-1] in kept and made[0] not in kept
    assert kept == sorted(kept, reverse=True)  # newest first


# --------------------------------------------------------------------------- #
# Restoring
# --------------------------------------------------------------------------- #

def test_restore_brings_the_old_world_back_and_drops_the_newer_one(tmp_path, monkeypatch):
    instance_id, idir = _seed(tmp_path, monkeypatch)
    meta = instance_backup.create_backup(instance_id)
    profile = idir / "profile" / "profile"

    # play on: the shop changes and a second save point appears
    (profile / "FFShopPricing" / "prices.json").write_text('{"ak": 999}')
    (profile / ".save" / "game" / "later.bin").write_bytes(b"newer")

    _fake_container_run(monkeypatch, idir)
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: True)
    out = instance_backup.restore_backup(instance_id, meta["id"])

    assert json.loads((profile / "FFShopPricing" / "prices.json").read_text())["ak"] == 100
    assert not (profile / ".save" / "game" / "later.bin").exists()  # not a mix of two worlds
    assert (profile / "ownerToken.bin").read_bytes() == b"secret-identity"  # identity kept
    assert (idir / "profile" / "logs" / "session1" / "console.log").exists()
    assert not (idir / instance_backup.EMBEDDED_META).exists()  # not left in the profile
    assert out["restored"]["id"] == meta["id"]


def test_restore_says_so_when_docker_is_the_thing_that_is_missing(tmp_path, monkeypatch):
    """Backing up needs no daemon; putting one back does, so name that (#179)."""
    instance_id, _ = _seed(tmp_path, monkeypatch)
    meta = instance_backup.create_backup(instance_id)  # works with Docker down
    with pytest.raises(instance_service.InstanceError, match="Docker is not reachable"):
        instance_backup.restore_backup(instance_id, meta["id"])


def test_restore_refuses_while_the_server_runs(tmp_path, monkeypatch):
    instance_id, _ = _seed(tmp_path, monkeypatch)
    meta = instance_backup.create_backup(instance_id)
    monkeypatch.setattr(instance_service, "container_status", lambda _id: "running")
    with pytest.raises(instance_service.InstanceError, match="Stop the server"):
        instance_backup.restore_backup(instance_id, meta["id"])


def test_a_crafted_backup_id_never_reaches_the_disk_or_the_shell(tmp_path, monkeypatch):
    instance_id, _ = _seed(tmp_path, monkeypatch)
    for crafted in ("../../etc/passwd", "20260101-000000'; rm -rf /", "..", ""):
        with pytest.raises(instance_service.InstanceError, match="Unknown backup"):
            instance_backup.archive_path(instance_id, crafted)


# --------------------------------------------------------------------------- #
# Uploading someone else's file
# --------------------------------------------------------------------------- #

def _tar_with(tmp_path, members, name="upload.tar.gz"):
    path = tmp_path / name
    with tarfile.open(path, "w:gz") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return path


def test_an_uploaded_backup_joins_the_shelf_with_what_it_says_about_itself(tmp_path, monkeypatch):
    instance_id, idir = _seed(tmp_path, monkeypatch)
    made = instance_backup.create_backup(instance_id)
    downloaded = tmp_path / "carried-off-box.tar.gz"
    downloaded.write_bytes((idir / "backups" / f"{made['id']}.tar.gz").read_bytes())
    instance_backup.delete_backup(instance_id, made["id"])

    monkeypatch.setattr(instance_backup, "_mint_id", lambda _d: "20260202-101010")
    meta = instance_backup.store_upload(instance_id, "carried-off-box.tar.gz", downloaded)

    assert meta["source"] == "upload"
    assert meta["template_name"] == "Antons Testserver"  # read back out of the file
    assert meta["files"] == 4
    assert [b["id"] for b in instance_backup.list_backups(instance_id)] == [meta["id"]]


def test_an_archive_that_escapes_the_profile_is_refused(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    escapes = tarfile.TarInfo("../../etc/cron.d/pwn")
    escapes.size = 3
    bad = _tar_with(tmp_path, [(escapes, b"x" * 3)])
    with pytest.raises(instance_service.InstanceError, match="will not unpack"):
        instance_backup.inspect_upload(bad)


def test_an_archive_with_a_symlink_is_refused(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    link = tarfile.TarInfo("profile/evil")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/shadow"
    bad = _tar_with(tmp_path, [(link, None)])
    with pytest.raises(instance_service.InstanceError, match="will not unpack"):
        instance_backup.inspect_upload(bad)


def test_an_archive_of_something_else_entirely_is_refused(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    stray = tarfile.TarInfo("holiday-photos/beach.jpg")
    stray.size = 3
    bad = _tar_with(tmp_path, [(stray, b"jpg")])
    with pytest.raises(instance_service.InstanceError, match="will not unpack"):
        instance_backup.inspect_upload(bad)

    (tmp_path / "notatar.tar.gz").write_bytes(b"this is not gzip")
    with pytest.raises(instance_service.InstanceError, match="not a readable"):
        instance_backup.inspect_upload(tmp_path / "notatar.tar.gz")


def test_an_archive_that_unpacks_to_more_than_the_cap_is_refused(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(instance_backup, "MAX_UPLOAD_BYTES", 100)
    big = tarfile.TarInfo("profile/huge.bin")
    big.size = 500
    bad = _tar_with(tmp_path, [(big, b"x" * 500)])
    with pytest.raises(instance_service.InstanceError, match="more than"):
        instance_backup.inspect_upload(bad)

# --------------------------------------------------------------------------- #
# Backups from another template (#181)
# --------------------------------------------------------------------------- #

def _add_template(name, scenario, hive=0):
    with Session(get_engine()) as session:
        template = Template(
            name=name,
            config_json=json.dumps({
                "game": {
                    "scenarioId": scenario,
                    "gameProperties": {"persistence": {"hiveId": hive}},
                }
            }),
        )
        session.add(template)
        session.commit()
        session.refresh(template)
        return template.id


def _repoint(instance_id, template_id):
    with Session(get_engine()) as session:
        inst = session.get(Instance, instance_id)
        inst.template_id = template_id
        session.add(inst)
        session.commit()


def test_a_backup_from_the_current_setup_is_marked_as_fitting(tmp_path, monkeypatch):
    instance_id, _ = _seed(tmp_path, monkeypatch)
    instance_backup.create_backup(instance_id)
    row = instance_backup.overview(instance_id)["backups"][0]
    assert row["fit"] == "match"
    assert row["switch_to"] is None  # nothing to change; it already loads


def test_a_backup_from_another_scenario_names_the_template_to_switch_back_to(tmp_path, monkeypatch):
    """The whole point of #181: the warning has to carry the way out."""
    instance_id, _ = _seed(tmp_path, monkeypatch)
    made = instance_backup.create_backup(instance_id)
    other = _add_template("Freedom Fighters", "{FFFF}Missions/FF.conf", hive=1)
    _repoint(instance_id, other)

    row = next(
        b for b in instance_backup.overview(instance_id)["backups"] if b["id"] == made["id"]
    )
    assert row["fit"] == "other-scenario"
    assert row["switch_to"]["name"] == "Antons Testserver"  # the one that wrote it
    assert row["switch_to"]["exact"] is True


def test_the_same_scenario_on_a_different_hive_is_a_different_save(tmp_path, monkeypatch):
    """Two templates, one scenario, two hive ids — the engine loads neither's world
    into the other, and before #181 nothing said so."""
    instance_id, _ = _seed(tmp_path, monkeypatch)
    made = instance_backup.create_backup(instance_id)
    scenario = "{59AD59368755F41A}Missions/21_GM_Eden.conf"
    _repoint(instance_id, _add_template("Same scenario, hive 7", scenario, hive=7))

    row = next(
        b for b in instance_backup.overview(instance_id)["backups"] if b["id"] == made["id"]
    )
    assert row["fit"] == "other-hive"
    assert row["switch_to"]["name"] == "Antons Testserver"


def test_no_template_here_writes_that_save_so_none_is_offered(tmp_path, monkeypatch):
    instance_id, _ = _seed(tmp_path, monkeypatch)
    made = instance_backup.create_backup(instance_id)
    with Session(get_engine()) as session:  # the template that wrote it is gone
        session.delete(session.get(Template, 1))
        session.commit()
    _repoint(instance_id, _add_template("Something else", "{ZZZZ}Missions/Z.conf"))

    row = next(
        b for b in instance_backup.overview(instance_id)["backups"] if b["id"] == made["id"]
    )
    assert row["fit"] == "other-scenario"
    assert row["switch_to"] is None  # say so rather than offering a wrong swap


def test_an_uploaded_archive_that_never_said_is_unknown_not_wrong(tmp_path, monkeypatch):
    instance_id, _ = _seed(tmp_path, monkeypatch)
    assert instance_backup.fit_of({}, {"scenario_id": "{A}x.conf"}) == "unknown"
    assert instance_backup._switch_candidate({}, [{"id": 1, "name": "t", "scenario_id": "{A}x.conf", "hive_id": 0}]) is None


def test_restoring_can_put_the_matching_template_back_in_the_same_step(tmp_path, monkeypatch):
    instance_id, idir = _seed(tmp_path, monkeypatch)
    made = instance_backup.create_backup(instance_id)
    other = _add_template("Freedom Fighters", "{FFFF}Missions/FF.conf", hive=1)
    _repoint(instance_id, other)
    (idir / "profile" / "profile" / "FFShopPricing" / "prices.json").write_text('{"ak": 999}')

    _fake_container_run(monkeypatch, idir)
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: True)
    monkeypatch.setattr(instance_service.docker_service, "find_instance_container", lambda _id: None)
    out = instance_backup.restore_backup(
        instance_id, made["id"], switch_template_id=made["template_id"]
    )

    assert out["switched_to"]["template_name"] == "Antons Testserver"
    with Session(get_engine()) as session:
        assert session.get(Instance, instance_id).template_id == made["template_id"]
    # ...and the world is the one the backup held, not the one played since
    prices = idir / "profile" / "profile" / "FFShopPricing" / "prices.json"
    assert json.loads(prices.read_text())["ak"] == 100
    assert instance_backup.overview(instance_id)["backups"][0]["fit"] == "match"


def test_a_restore_can_keep_a_copy_of_the_world_it_replaces(tmp_path, monkeypatch):
    instance_id, idir = _seed(tmp_path, monkeypatch)
    made = instance_backup.create_backup(instance_id)
    (idir / "profile" / "profile" / "FFShopPricing" / "prices.json").write_text('{"ak": 999}')

    _fake_container_run(monkeypatch, idir)
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: True)
    out = instance_backup.restore_backup(instance_id, made["id"], backup_first=True)

    assert out["safety_backup"]["source"] == "pre-restore"
    ids = [b["id"] for b in instance_backup.list_backups(instance_id)]
    assert made["id"] in ids and out["safety_backup"]["id"] in ids


def test_the_safety_copy_cannot_prune_the_backup_being_restored(tmp_path, monkeypatch):
    """The shelf is capped, so the oldest goes when a new one lands — and the
    oldest may be exactly the archive about to be unpacked."""
    instance_id, idir = _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(instance_backup, "KEEP", 3)
    made = []
    for n in range(3):
        monkeypatch.setattr(
            instance_backup, "_mint_id", lambda _d, n=n: f"20260101-0000{n:02d}"
        )
        made.append(instance_backup.create_backup(instance_id)["id"])
    oldest = made[0]

    monkeypatch.setattr(instance_backup, "_mint_id", lambda _d: "20260101-000099")
    _fake_container_run(monkeypatch, idir)
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: True)
    out = instance_backup.restore_backup(instance_id, oldest, backup_first=True)

    assert out["restored"]["id"] == oldest
    assert oldest in [b["id"] for b in instance_backup.list_backups(instance_id)]

# --------------------------------------------------------------------------- #
# Forcing a world where the rules say it does not belong (#184)
# --------------------------------------------------------------------------- #

def test_a_restore_that_would_not_load_is_refused_and_says_why(tmp_path, monkeypatch):
    """The fit rules used to be advice the GUI drew; now the service enforces them."""
    instance_id, idir = _seed(tmp_path, monkeypatch)
    made = instance_backup.create_backup(instance_id)
    _repoint(instance_id, _add_template("Freedom Fighters", "{FFFF}Missions/FF.conf", hive=1))

    _fake_container_run(monkeypatch, idir)
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: True)
    with pytest.raises(instance_service.InstanceError, match="does not read another scenario"):
        instance_backup.restore_backup(instance_id, made["id"])


def test_a_hive_mismatch_is_refused_in_its_own_words(tmp_path, monkeypatch):
    instance_id, idir = _seed(tmp_path, monkeypatch)
    made = instance_backup.create_backup(instance_id)
    scenario = "{59AD59368755F41A}Missions/21_GM_Eden.conf"
    _repoint(instance_id, _add_template("Same scenario, hive 7", scenario, hive=7))

    _fake_container_run(monkeypatch, idir)
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: True)
    with pytest.raises(instance_service.InstanceError, match="hive id 7"):
        instance_backup.restore_backup(instance_id, made["id"])


def test_force_puts_the_world_where_the_operator_says(tmp_path, monkeypatch):
    """The rule is right almost always; the operator is right the rest of the time."""
    instance_id, idir = _seed(tmp_path, monkeypatch)
    made = instance_backup.create_backup(instance_id)
    other = _add_template("Freedom Fighters", "{FFFF}Missions/FF.conf", hive=1)
    _repoint(instance_id, other)
    (idir / "profile" / "profile" / "FFShopPricing" / "prices.json").write_text('{"ak": 999}')

    _fake_container_run(monkeypatch, idir)
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: True)
    out = instance_backup.restore_backup(instance_id, made["id"], force=True)

    assert out["forced"] is True and out["fit"] == "other-scenario"
    prices = idir / "profile" / "profile" / "FFShopPricing" / "prices.json"
    assert json.loads(prices.read_text())["ak"] == 100  # the backup's world is in place
    with Session(get_engine()) as session:  # ...and the template was left alone
        assert session.get(Instance, instance_id).template_id == other


def test_a_forced_restore_always_keeps_the_world_it_replaces(tmp_path, monkeypatch):
    """backup_first is not the operator's call on the path most likely to be wrong."""
    instance_id, idir = _seed(tmp_path, monkeypatch)
    made = instance_backup.create_backup(instance_id)
    _repoint(instance_id, _add_template("Freedom Fighters", "{FFFF}Missions/FF.conf", hive=1))

    _fake_container_run(monkeypatch, idir)
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: True)
    out = instance_backup.restore_backup(
        instance_id, made["id"], force=True, backup_first=False
    )

    assert out["safety_backup"] is not None
    assert out["safety_backup"]["source"] == "pre-restore"


def test_force_can_pair_a_world_with_any_template_at_all(tmp_path, monkeypatch):
    """Not just the one that matches: a scenario id can move, a mod can relocate
    its data, and the operator may be transplanting a world on purpose."""
    instance_id, idir = _seed(tmp_path, monkeypatch)
    made = instance_backup.create_backup(instance_id)
    unrelated = _add_template("Nothing to do with it", "{ZZZZ}Missions/Z.conf", hive=3)

    _fake_container_run(monkeypatch, idir)
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: True)
    monkeypatch.setattr(instance_service.docker_service, "find_instance_container", lambda _id: None)
    out = instance_backup.restore_backup(
        instance_id, made["id"], switch_template_id=unrelated, force=True
    )

    assert out["forced"] is True
    assert out["switched_to"]["template_name"] == "Nothing to do with it"
    with Session(get_engine()) as session:
        assert session.get(Instance, instance_id).template_id == unrelated


def test_an_archive_that_never_said_what_wrote_it_needs_force(tmp_path, monkeypatch):
    instance_id, idir = _seed(tmp_path, monkeypatch)
    made = instance_backup.create_backup(instance_id)
    meta_file = idir / "backups" / f"{made['id']}.json"
    meta_file.write_text(json.dumps({"id": made["id"]}))  # an upload with no origin

    _fake_container_run(monkeypatch, idir)
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: True)
    with pytest.raises(instance_service.InstanceError, match="does not record which scenario"):
        instance_backup.restore_backup(instance_id, made["id"])
    assert instance_backup.restore_backup(instance_id, made["id"], force=True)["fit"] == "unknown"


def test_choosing_the_template_already_attached_does_not_rebuild_the_container(tmp_path, monkeypatch):
    """set_instance_template tears the container down; asking for the template the
    instance already has must not pay that price."""
    instance_id, idir = _seed(tmp_path, monkeypatch)
    made = instance_backup.create_backup(instance_id)
    repointed = []
    monkeypatch.setattr(
        instance_service, "set_instance_template",
        lambda *a: repointed.append(a),
    )
    _fake_container_run(monkeypatch, idir)
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: True)

    out = instance_backup.restore_backup(
        instance_id, made["id"], switch_template_id=made["template_id"]
    )

    assert repointed == [] and out["switched_to"] is None and out["forced"] is False


def test_metadata_left_without_its_archive_is_swept_up(tmp_path, monkeypatch):
    """A lone sidecar is invisible to the listing (which reads archives) and would
    sit there for ever, so opening the card tidies up after it (#187)."""
    instance_id, idir = _seed(tmp_path, monkeypatch)
    instance_backup.create_backup(instance_id)
    stray = idir / "backups" / "20250101-000000.json"
    stray.write_text('{"id": "20250101-000000"}')

    assert len(instance_backup.overview(instance_id)["backups"]) == 1
    assert not stray.exists()
