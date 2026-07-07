from pathlib import Path

import config
from services.steam_service import parse_line, steam


def test_parse_progress_line():
    line = " Update state (0x61) downloading, progress: 26.34 (2416320529 / 9173824512)"
    ev = parse_line(line)
    assert ev == {
        "kind": "progress",
        "phase": "downloading",
        "percent": 26.34,
        "bytes_done": 2416320529,
        "bytes_total": 9173824512,
    }


def test_parse_verifying_line():
    line = " Update state (0x81) verifying update, progress: 99.90 (9164 / 9173)"
    ev = parse_line(line)
    assert ev["kind"] == "progress"
    assert ev["phase"] == "verifying update"


def test_parse_success_line():
    assert parse_line("Success! App '1874900' fully installed.") == {"kind": "success"}


def test_parse_error_line():
    ev = parse_line("ERROR! Failed to install app '1874900' (No subscription)")
    assert ev["kind"] == "error"
    assert "No subscription" in ev["message"]


def test_parse_noise_returns_none():
    for line in ("Redirecting stderr to ...", "Loading Steam API...OK", ""):
        assert parse_line(line) is None


def test_parse_latest_build_picks_public_branch():
    from services.steam_service import parse_latest_build

    vdf = '''
    "1874900"
    {
        "depots"
        {
            "branches"
            {
                "public"
                {
                    "buildid"		"19352300"
                    "timeupdated"	"1751884800"
                }
                "experimental"
                {
                    "buildid"		"99999999"
                }
            }
        }
    }
    '''
    assert parse_latest_build(vdf) == "19352300"


def test_parse_latest_build_none_when_absent():
    from services.steam_service import parse_latest_build

    assert parse_latest_build("no branches here") is None


def test_installed_info_missing_manifest():
    assert steam.installed_info("stable") is None


def test_installed_info_reads_manifest():
    app_id = config.BRANCHES["stable"]["app_id"]
    steamapps = Path(config.settings.serverfiles_dir) / "stable" / "steamapps"
    steamapps.mkdir(parents=True, exist_ok=True)
    (steamapps / f"appmanifest_{app_id}.acf").write_text(
        '"AppState"\n{\n'
        f'\t"appid"\t\t"{app_id}"\n'
        '\t"buildid"\t\t"19352300"\n'
        '\t"LastUpdated"\t\t"1751884800"\n'
        '\t"SizeOnDisk"\t\t"9173824512"\n'
        "}\n"
    )
    info = steam.installed_info("stable")
    assert info == {
        "app_id": app_id,
        "build_id": "19352300",
        "last_updated": 1751884800,
        "size_bytes": 9173824512,
    }
