# Features

Reforger Server Manager brings the common setup and day-to-day operations for Arma Reforger Dedicated Servers into one browser interface.

[Back to README](../README.md) · [Installation guide](installation.md) · [Interactive architecture overview](https://tubalainen.github.io/reforger-server-manager/architecture/reforger-server-manager-overview.html)

## Server templates

Templates define how servers are created. They cover the scenario, Workshop mods, player limits, mission headers, admins, whitelist, bans, RCON, operating settings, and supported custom `config.json` fields.

![Server templates](images/01-server-templates.png)

![Template scenario](images/02-template-scenario.png)

![Template mods](images/03-template-mods.png)

![Template settings](images/04-template-settings.png)

Key template capabilities include:

- Search official and community scenarios and set per-scenario player limits.
- Start each new template with a distinguishable in-game server name instead of one
  every server shares, re-rollable in the wizard.
- Search the Workshop, fetch metadata, resolve dependencies, and preserve dependency order.
- Drag mods into a load order, or ask an AI for one: paste the prompt into any free web
  assistant, or answer it in one click with a local Ollama on your own GPU or any
  OpenAI-compatible service.
- Lock mod versions or follow current releases.
- Import an existing server `config.json` or export a generated configuration.
- Create reusable mod lists and apply them to server templates.
- Track known mods, dependency relationships, usage, update status, and local Workshop storage.
- Keep template change logs and see which servers still use an older revision.

## Server operations

Each server gets its own ports, configuration, profile, Workshop content, and lifecycle controls while sharing downloaded server binaries where appropriate.

The **Servers** page is the overview of the whole host:

- Servers online, players, and CPU and memory across all servers, with a sparkline of the last hour for each.
- One *Needs attention* list — templates edited since a server started, server files not downloaded, new server releases — each with the button that fixes it.
- A row per server with status, players against the limit, FPS, CPU, memory, uptime and next scheduled restart, plus Start, Stop, Restart and Delete.
- Restart every running server at once, after a confirmation.

![Servers overview](images/06-servers-overview.png)

![A server's page](images/05-instance-detail.png)

A server's own page keeps every server listed down the left and splits into five tabs — Overview, Console, Players, Saves and Settings. Settings is one form with one Save: changed fields are marked, fields that need the server stopped are locked with the reason, and leaving with unsaved changes asks first. From it you can:

- Start, stop, restart, and delete a server.
- Follow live logs and copy useful diagnostic output.
- View player count, process state, resource use, and server metadata.
- Recover servers after manager or host restarts.
- Configure auto-start and scheduled restarts.
- Apply template changes to existing servers.
- See assigned game, A2S, and RCON ports without managing leases manually.
- Back up and restore the saved game data — the world, plus the databases the scenario
  and its mods keep beside it — download a backup or upload one back, and have the
  manager take one automatically before you switch the server to another template.
- Restore a world from a template the server no longer runs: each backup is matched
  against the current scenario and hive id, and the template that reads it can be put
  back in the same action.
- Choose the template a world is restored under, and force a combination the manager
  would refuse — with the reason shown, the world it replaces always kept, and the
  forced restore recorded in the log.

## Downloads and updates

All of this lives under **System › Server files**.

- Download stable and experimental Arma Reforger server files with SteamCMD.
- Watch download progress and recent output in real time.
- Check daily for server and Workshop updates.
- Optionally download new builds automatically and restart affected servers.
- Pull or replace the configurable server runtime image independently of manager updates.

## Configuration and data

- Edit known settings with forms or use the inline JSON editor.
- Preserve valid unknown top-level keys and custom `game` or `operating` properties during form edits.
- Validate the generated configuration before saving it.
- Back up and restore every server template and mod list as one file.
- Inspect Workshop storage and remove content that is no longer required.

Port values are assigned per server by the manager. Imported port settings therefore do not override a server's port lease.

## Access and safety

- Password-based browser authentication with session management and login rate limiting.
- Fail-closed defaults when the GUI is exposed beyond localhost.
- A logout-all endpoint for invalidating every active session.
- Support for trusted reverse proxies and delegated authentication.
- A least-privilege Docker socket proxy instead of mounting the daemon socket into the manager.
- `no-new-privileges` on manager-created containers.
- Edit locks that prevent two browser sessions from changing the same template simultaneously.

For deployment requirements and the security model, read the [installation guide](installation.md#security).
