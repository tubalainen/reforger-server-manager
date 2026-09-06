# Features

Reforger Server Manager brings the common setup and day-to-day operations for Arma Reforger Dedicated Servers into one browser interface.

[Back to README](../README.md) · [Installation guide](installation.md) · [Interactive architecture overview](https://tubalainen.github.io/reforger-server-manager/architecture/reforger-server-manager-overview.html)

## Server templates

Templates define how instances are created. They cover the scenario, Workshop mods, player limits, mission headers, admins, whitelist, bans, RCON, operating settings, and supported custom `config.json` fields.

![Server templates](images/01-server-templates.png)

![Template scenario](images/02-template-scenario.png)

![Template mods](images/03-template-mods.png)

![Template settings](images/04-template-settings.png)

Key template capabilities include:

- Search official and community scenarios and set per-scenario player limits.
- Search the Workshop, fetch metadata, resolve dependencies, and preserve dependency order.
- Lock mod versions or follow current releases.
- Import an existing server `config.json` or export a generated configuration.
- Create reusable mod lists and apply them to server templates.
- Track known mods, dependency relationships, usage, update status, and local Workshop storage.
- Keep template change logs and see which instances still use an older revision.

## Instance operations

Each instance gets its own ports, configuration, profile, Workshop content, and lifecycle controls while sharing downloaded server binaries where appropriate.

![Instance detail](images/05-instance-detail.png)

From the instance view you can:

- Start, stop, restart, and delete a server.
- Follow live logs and copy useful diagnostic output.
- View player count, process state, resource use, and server metadata.
- Recover instances after manager or host restarts.
- Configure auto-start and scheduled restarts.
- Apply template changes to existing instances.
- See assigned game, A2S, and RCON ports without managing leases manually.
- Back up and restore the saved game data — the world, plus the databases the scenario
  and its mods keep beside it — download a backup or upload one back, and have the
  manager take one automatically before you switch the instance to another template.
- Restore a world from a template the server no longer runs: each backup is matched
  against the current scenario and hive id, and the template that reads it can be put
  back in the same action.
- Choose the template a world is restored under, and force a combination the manager
  would refuse — with the reason shown, the world it replaces always kept, and the
  forced restore recorded in the log.

## Downloads and updates

- Download stable and experimental Arma Reforger server files with SteamCMD.
- Watch download progress and recent output in real time.
- Check daily for server and Workshop updates.
- Optionally download new builds automatically and restart affected instances.
- Pull or replace the configurable server runtime image independently of manager updates.

## Configuration and data

- Edit known settings with forms or use the inline JSON editor.
- Preserve valid unknown top-level keys and custom `game` or `operating` properties during form edits.
- Validate the generated configuration before saving it.
- Back up and restore every server template and mod template as one file.
- Inspect Workshop storage and remove content that is no longer required.

Port values are assigned per instance by the manager. Imported port settings therefore do not override an instance's port lease.

## Access and safety

- Password-based browser authentication with session management and login rate limiting.
- Fail-closed defaults when the GUI is exposed beyond localhost.
- A logout-all endpoint for invalidating every active session.
- Support for trusted reverse proxies and delegated authentication.
- A least-privilege Docker socket proxy instead of mounting the daemon socket into the manager.
- `no-new-privileges` on manager-created containers.
- Edit locks that prevent two browser sessions from changing the same template simultaneously.

For deployment requirements and the security model, read the [installation guide](installation.md#security).
