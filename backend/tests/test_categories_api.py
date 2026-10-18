"""Game-mode categories for server templates (#223, phase C)."""
import json

from sqlmodel import Session, select

import models


def _spec(name="Conflict", **over):
    return {
        "name": name,
        "description": "",
        "scenario_id": "{ECC61978EDCC2B5A}Missions/23_Campaign.conf",
        "mods": [],
        "max_players": 32,
    } | over


def _category(client, name):
    r = client.post("/api/template-categories", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _summaries(client, tid):
    return [e["summary"] for e in client.get(f"/api/templates/{tid}/changelog").json()]


def test_categories_require_auth(client):
    assert client.get("/api/template-categories").status_code == 401


def test_the_built_ins_are_seeded_once():
    # _clean_db wipes both the categories and the "seeded" flag, so seed here.
    engine = models.get_engine()
    assert models.seed_template_categories(engine) == 6
    with Session(engine) as s:
        names = {c.name for c in s.exec(select(models.TemplateCategory)).all()}
        assert names == {"Game Master", "HQC", "Conflict", "COE", "Escapist", "Freedom Fighters"}
        s.delete(s.exec(select(models.TemplateCategory).where(
            models.TemplateCategory.name == "COE")).one())
        s.commit()
    # A deleted built-in is not brought back on the next start.
    assert models.seed_template_categories(engine) == 0
    with Session(engine) as s:
        assert "COE" not in {c.name for c in s.exec(select(models.TemplateCategory)).all()}


def test_add_rename_and_refuse_duplicates(logged_in):
    # the app has seeded the built-ins on startup; "conflict" clashes with one
    assert logged_in.post("/api/template-categories", json={"name": "conflict"}).status_code == 409
    hqc = _category(logged_in, "  Milsim   night  ")
    assert hqc["name"] == "Milsim night"
    assert logged_in.post("/api/template-categories", json={"name": "milsim NIGHT"}).status_code == 409
    assert logged_in.post("/api/template-categories", json={"name": "   "}).status_code == 422
    other = _category(logged_in, "Training")
    r = logged_in.put(f"/api/template-categories/{other['id']}", json={"name": "Trainings"})
    assert r.json()["name"] == "Trainings"
    assert logged_in.put(
        f"/api/template-categories/{other['id']}", json={"name": "HQC"}
    ).status_code == 409
    names = [c["name"] for c in logged_in.get("/api/template-categories").json()]
    assert names == sorted(names, key=str.lower)
    assert {"Milsim night", "Trainings", "HQC"} <= set(names)


def test_a_template_is_filed_under_a_category(logged_in):
    hqc = _category(logged_in, "Milsim")
    t = logged_in.post("/api/templates", json=_spec(category_id=hqc["id"])).json()
    assert (t["category_id"], t["category"]) == (hqc["id"], "Milsim")
    row = next(x for x in logged_in.get("/api/templates").json() if x["id"] == t["id"])
    assert row["category"] == "Milsim"
    counts = {c["name"]: c["template_count"] for c in logged_in.get("/api/template-categories").json()}
    assert counts["Milsim"] == 1
    bad = logged_in.post("/api/templates", json=_spec("Other", category_id=999))
    assert bad.status_code == 400


def test_a_save_without_a_category_keeps_it_and_a_change_is_logged(logged_in):
    hqc = _category(logged_in, "Milsim")
    coe = _category(logged_in, "Training")
    t = logged_in.post("/api/templates", json=_spec(category_id=hqc["id"])).json()
    logged_in.put(f"/api/templates/{t['id']}", json=_spec(max_players=40))
    assert logged_in.get(f"/api/templates/{t['id']}").json()["category"] == "Milsim"
    logged_in.put(f"/api/templates/{t['id']}", json=_spec(max_players=40, category_id=coe["id"]))
    assert "Category: Milsim → Training" in _summaries(logged_in, t["id"])


def test_a_prod_template_can_change_category_without_a_release(logged_in):
    gm = _category(logged_in, "Milsim")
    t = logged_in.post("/api/templates", json=_spec()).json()
    logged_in.post(f"/api/templates/{t['id']}/promote", json={})
    prod = logged_in.post(f"/api/templates/{t['id']}/promote", json={}).json()

    r = logged_in.put(f"/api/templates/{t['id']}/category", json={"category_id": gm["id"]})
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["category"] == "Milsim"
    assert after["updated_at"] == prod["updated_at"]  # servers aren't told it changed
    assert "Category: Uncategorised → Milsim" in _summaries(logged_in, t["id"])
    # still locked for everything else
    assert logged_in.put(f"/api/templates/{t['id']}", json=_spec()).status_code == 409


def test_copies_keep_the_category(logged_in):
    hqc = _category(logged_in, "Milsim")
    t = logged_in.post("/api/templates", json=_spec(category_id=hqc["id"])).json()
    copy = logged_in.post(f"/api/templates/{t['id']}/copy").json()
    assert copy["category"] == "Milsim"


def test_deleting_a_category_leaves_its_templates_uncategorised(logged_in):
    hqc = _category(logged_in, "Milsim")
    t = logged_in.post("/api/templates", json=_spec(category_id=hqc["id"])).json()
    r = logged_in.delete(f"/api/template-categories/{hqc['id']}")
    assert r.json() == {"uncategorised": 1}
    assert logged_in.get(f"/api/templates/{t['id']}").json()["category_id"] is None
    assert "Category: Milsim → Uncategorised (category deleted)" in _summaries(logged_in, t["id"])


def test_backup_carries_the_category_by_name(logged_in):
    hqc = _category(logged_in, "Milsim")
    logged_in.post("/api/templates", json=_spec("Op", category_id=hqc["id"]))
    payload = json.loads(logged_in.get("/api/backup/export").text)
    assert payload["server_templates"][0]["category"] == "Milsim"

    with Session(models.get_engine()) as s:
        for model in (models.TemplateChange, models.Template, models.TemplateCategory):
            for row in s.exec(select(model)).all():
                s.delete(row)
        s.commit()
    r = logged_in.post("/api/backup/import", json={
        "backup": payload, "decisions": {"server_templates": {"Op": "create"}},
    })
    assert r.status_code == 200, r.text
    assert logged_in.get("/api/templates").json()[0]["category"] == "Milsim"  # recreated
