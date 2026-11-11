"""Which mod ships a script file, read from the paks' file tables (#270)."""
from pak_helper import write_pak

from services import pak_index

SCRIPT = "scripts/Game/Player/RO_V_PlayerController.c"


def test_a_paks_file_table_lists_nested_paths(tmp_path):
    pak = write_pak(tmp_path / "data.pak", [
        SCRIPT, "scripts/Game/RO_Other.c", "Prefabs/Car.et", "addon.gproj"])
    table = pak_index.read(pak)
    assert table.paths == {
        SCRIPT.lower(), "scripts/game/ro_other.c", "prefabs/car.et", "addon.gproj"}
    assert table.contains(SCRIPT) and table.contains(SCRIPT.upper())
    assert not table.contains("scripts/Game/Player/Other.c")


def test_a_first_folder_with_a_name_of_its_own_still_matches(tmp_path):
    pak = write_pak(tmp_path / "data.pak", ["RealismOverhaulVehicles/" + SCRIPT])
    table = pak_index.read(pak)
    assert table.contains(SCRIPT)
    assert not table.contains("Player/RO_V_PlayerController.c".replace("Player/", "layer/"))


def test_only_the_table_is_read_past_a_large_data_chunk(tmp_path):
    pak = write_pak(tmp_path / "data.pak", [SCRIPT], data=b"\1\x17RO_V_Nope.c" * 50_000)
    assert pak_index.read(pak).paths == {SCRIPT.lower()}


def test_a_table_that_does_not_parse_is_searched_for_the_files_name(tmp_path):
    pak = write_pak(tmp_path / "data.pak", [SCRIPT])
    raw = bytearray(pak.read_bytes())
    at = raw.index(b"FILE") + 8
    raw[at] = 7  # not a folder, not a file
    pak.write_bytes(raw)
    table = pak_index.read(pak)
    assert table.paths is None
    assert table.contains(SCRIPT) and not table.contains("scripts/Game/Missing.c")


def test_a_file_that_is_no_pak_holds_nothing(tmp_path):
    for name, raw in (("empty.pak", b""), ("junk.pak", b"not a pak at all" * 10),
                      ("cut.pak", b"FORM\0\0\0\x10PAC1HEAD\0\0\0\x1c")):
        (tmp_path / name).write_bytes(raw)
        table = pak_index.read(tmp_path / name)
        assert table is not None and not table.contains(SCRIPT)
    assert pak_index.read(tmp_path / "missing.pak") is None


def test_owners_names_every_mod_that_ships_the_file(tmp_path):
    a, b, c = tmp_path / "A_1", tmp_path / "B_2", tmp_path / "C_3"
    write_pak(a / "data.pak", ["scripts/Game/Other.c"])
    write_pak(a / "data001.pak", [SCRIPT])       # a mod split over several paks
    write_pak(b / "data.pak", ["scripts/Game/B.c"])
    (c / "scripts" / "Game").mkdir(parents=True)  # an unpacked mod
    (c / "scripts" / "Game" / "Loose.c").write_text("x")

    out = pak_index.owners({"A": [a], "B": [b], "C": [c]},
                           [SCRIPT, "scripts/Game/Loose.c", "scripts/Game/Base.c", SCRIPT])

    assert out == {SCRIPT: ["A"], "scripts/Game/Loose.c": ["C"], "scripts/Game/Base.c": []}


def test_a_path_out_of_the_mod_folder_is_not_looked_up(tmp_path):
    (tmp_path / "secret.c").write_text("x")
    mod = tmp_path / "A_1"
    mod.mkdir()
    assert pak_index.owners({"A": [mod]}, ["../secret.c", "/etc/passwd", "C:/x.c"]) == {}
