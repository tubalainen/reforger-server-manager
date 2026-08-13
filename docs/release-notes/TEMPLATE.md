# How to write release notes for this project

Copy the skeleton below to `docs/release-notes/v<version>.md`. The release
workflow publishes that file as the GitHub release body, and re-publishes it
whenever the file and the release drift apart — so fixing a notes file after the
fact does reach readers.

## The one rule that is enforced

**Users never clone this repository.** They downloaded `docker-compose.yaml`
once and copied `.env.example` to `.env` once. `docker compose pull`, `rsm
update` and the Windows start script only refresh the *image* — nothing on their
disk changes. So when a release edits `.env.example` or a compose file, the
release notes are the only thing that can tell them to apply it themselves.

`scripts/ci/release_guard.py` runs in CI and fails the build when

* `.env.example`, a `docker-compose*.yaml` or `scripts/linux/rsm.sh` changed
  since the last released tag, and
* the notes file for the current `APP_VERSION` is missing, lacks the
  `## Updating your setup files` section, or never names a newly added `.env`
  key.

When it fails it prints a ready-made section listing exactly which keys and
files changed. Run it yourself any time:

```bash
python scripts/ci/release_guard.py
```

It stays quiet for the ordinary case — a release that only changes code inside
the image needs no such section, because pulling the image *is* the update.

---

## Skeleton

```markdown
## What's new in <version> — <one line a player would understand>

<Two or three sentences on what changed and why anyone should care. Link the
issue: ([#123](https://github.com/tubalainen/reforger-server-manager/issues/123)).>

## Do I need to do anything?

**No.** / **Yes — see below.** <Say plainly whether an existing install keeps
working untouched. If something must be done, say what, here, in one paragraph.>

## Updating your setup files

<REQUIRED when .env.example or a compose file changed; omit entirely otherwise.>

This release adds settings to `.env`. Your existing `.env` is never overwritten,
so add them by hand (or copy the new lines out of
[.env.example](https://github.com/tubalainen/reforger-server-manager/blob/main/.env.example)):

```bash
NEW_KEY=
```

The compose file changed too — pulling the image does not update it. Follow
[Updating your setup files](https://github.com/tubalainen/reforger-server-manager#updating-your-setup-files)
for your install type.

---

## <Feature heading>

<Body. House style: explain the mechanism, be honest about what is not known,
prefer plain language over marketing.>

## Small print

- <Migration details, defaults that changed, anything that could surprise.>
```
