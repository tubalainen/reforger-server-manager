from pathlib import Path

import pytest

from services import workshop_service as ws

FIXTURES = Path(__file__).parent / "fixtures"


def _props(name: str) -> dict:
    return ws.extract_next_data((FIXTURES / name).read_text(encoding="utf-8"))


def test_normalize_asset_id_variants():
    assert ws.normalize_asset_id("5965550F24A0C152") == "5965550F24A0C152"
    assert ws.normalize_asset_id("5965550f24a0c152-WhereAmI") == "5965550F24A0C152"
    assert ws.normalize_asset_id(
        "https://reforger.armaplatform.com/workshop/5965550F24A0C152-WhereAmI"
    ) == "5965550F24A0C152"
    assert ws.normalize_asset_id("nonsense") is None


def test_extract_build_id():
    build = ws.extract_build_id((FIXTURES / "asset_simple.html").read_text(encoding="utf-8"))
    assert build and isinstance(build, str)


def test_extract_next_data_rejects_garbage():
    with pytest.raises(ValueError):
        ws.extract_next_data("<html>no next data here</html>")


def test_parse_search_fixture():
    result = ws.parse_search(_props("search_conflict.html"))
    assert result["count"] > 0
    assert len(result["rows"]) > 0
    row = result["rows"][0]
    assert set(row) >= {"id", "name", "type", "version", "size", "tags", "has_scenarios"}
    assert all(len(r["id"]) == 16 for r in result["rows"])


def test_parse_asset_simple_no_scenarios():
    asset = ws.parse_asset(_props("asset_simple.html"))
    assert asset["id"] == "5965550F24A0C152"
    assert asset["name"] == "Where Am I"
    assert asset["version"] == "1.2.0"
    assert asset["scenarios"] == []
    assert asset["dependencies"] == []
    # published version history (newest first) feeds the lock picker (#60)
    assert asset["versions"] == ["1.2.0", "1.1.0", "1.0.3", "1.0.2", "1.0.1", "1.0.0"]


def test_parse_asset_with_scenarios_and_deps():
    asset = ws.parse_asset(_props("asset_with_scenarios.html"))
    assert asset["name"] == "NUSANTARA MAP CONFLICT"
    # scenarios carry the config.json scenarioId
    assert len(asset["scenarios"]) == 4
    sc = asset["scenarios"][0]
    assert sc["scenario_id"].startswith("{") and ".conf" in sc["scenario_id"]
    assert sc["name"]
    # dependencies expose id/name/version for config.json mods[]
    assert len(asset["dependencies"]) == 8
    dep = asset["dependencies"][0]
    assert len(dep["id"]) == 16 and dep["version"]


def test_parse_asset_rejects_missing_asset():
    with pytest.raises(ValueError):
        ws.parse_asset({"foo": "bar"})


def test_resolve_dependencies_flattens_and_dedupes(monkeypatch):
    # root A depends on B and C; B depends on C (shared) -> C appears once
    graph = {
        "AAAAAAAAAAAAAAAA": {"id": "AAAAAAAAAAAAAAAA", "name": "A", "version": "1.0", "size": 10,
                             "scenarios": [{"scenario_id": "{Z}Missions/a.conf", "name": "A"}],
                             "dependencies": [
                                 {"id": "BBBBBBBBBBBBBBBB", "name": "B", "version": "2.0", "size": 5},
                                 {"id": "CCCCCCCCCCCCCCCC", "name": "C", "version": "3.0", "size": 7},
                             ]},
        "BBBBBBBBBBBBBBBB": {"id": "BBBBBBBBBBBBBBBB", "name": "B", "version": "2.0", "size": 5,
                             "dependencies": [
                                 {"id": "CCCCCCCCCCCCCCCC", "name": "C", "version": "3.0", "size": 7},
                             ]},
        "CCCCCCCCCCCCCCCC": {"id": "CCCCCCCCCCCCCCCC", "name": "C", "version": "3.0", "size": 7,
                             "versions": ["3.0", "2.9"],
                             "dependencies": []},
    }
    monkeypatch.setattr(ws.workshop, "get_asset", lambda aid, use_cache=True: graph[aid])
    result = ws.workshop.resolve_dependencies("AAAAAAAAAAAAAAAA")
    ids = [m["modId"] for m in result["mods"]]
    assert ids[0] == "AAAAAAAAAAAAAAAA"  # root first
    assert sorted(ids) == ["AAAAAAAAAAAAAAAA", "BBBBBBBBBBBBBBBB", "CCCCCCCCCCCCCCCC"]
    assert len(ids) == 3  # C deduped
    assert result["missing"] == []
    # the requested asset is reported as the root (#55)
    assert result["root"] == "AAAAAAAAAAAAAAAA"
    # each mod carries its direct dependency edges for the mod manager
    by_id = {m["modId"]: m for m in result["mods"]}
    assert sorted(by_id["AAAAAAAAAAAAAAAA"]["dependencies"]) == [
        "BBBBBBBBBBBBBBBB", "CCCCCCCCCCCCCCCC",
    ]
    assert by_id["BBBBBBBBBBBBBBBB"]["dependencies"] == ["CCCCCCCCCCCCCCCC"]
    assert by_id["CCCCCCCCCCCCCCCC"]["dependencies"] == []
    # version history rides along for the lock picker (#60); absent -> []
    assert by_id["CCCCCCCCCCCCCCCC"]["versions"] == ["3.0", "2.9"]
    assert by_id["BBBBBBBBBBBBBBBB"]["versions"] == []
    # scenario-providing assets are flagged so the UI can warn about a second
    # scenario added as a mod (#69); content-only deps are not
    assert by_id["AAAAAAAAAAAAAAAA"]["provides_scenarios"] is True
    assert by_id["BBBBBBBBBBBBBBBB"]["provides_scenarios"] is False
    assert by_id["CCCCCCCCCCCCCCCC"]["provides_scenarios"] is False


def test_resolve_dependencies_reports_missing(monkeypatch):
    root = {"id": "AAAAAAAAAAAAAAAA", "name": "A", "version": "1.0", "size": 1,
            "dependencies": [{"id": "DDDDDDDDDDDDDDDD", "name": "D", "version": "1.0", "size": 1}]}

    def fake_get(aid, use_cache=True):
        if aid == "AAAAAAAAAAAAAAAA":
            return root
        raise ws.WorkshopError("boom")

    monkeypatch.setattr(ws.workshop, "get_asset", fake_get)
    result = ws.workshop.resolve_dependencies("AAAAAAAAAAAAAAAA")
    assert result["missing"] == ["DDDDDDDDDDDDDDDD"]
    # D is still listed (from the parent's dep entry) so the user sees it
    assert {m["modId"] for m in result["mods"]} == {"AAAAAAAAAAAAAAAA", "DDDDDDDDDDDDDDDD"}
