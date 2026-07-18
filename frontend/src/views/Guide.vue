<script setup>
// User guide + FAQ (issue #63). Pure static content — everything here should
// describe what the app actually does; update it when behaviour changes.
const links = [
  {
    href: 'https://community.bistudio.com/wiki/Arma_Reforger:Server_Config',
    title: 'Bohemia Interactive — Server Config reference',
    desc: 'The official config.json schema that templates render to.',
  },
  {
    href: 'https://community.bistudio.com/wiki/Arma_Reforger:Server_Hosting',
    title: 'Bohemia Interactive — Server Hosting guide',
    desc: 'Official hosting guidance, hardware requirements and Steam app ids.',
  },
  {
    href: 'https://reforger.armaplatform.com/workshop',
    title: 'Arma Reforger Workshop',
    desc: 'Browse scenarios and mods — the same catalogue the wizard searches.',
  },
  {
    href: 'https://github.com/tubalainen/reforger-server-manager',
    title: 'Reforger Server Manager on GitHub',
    desc: 'Source code, README, changelog and issue tracker for this app.',
  },
  {
    href: 'https://github.com/acemod/docker-reforger',
    title: 'ACE Mod docker-reforger',
    desc: 'The default Docker image every server instance runs from.',
  },
]

const faq = [
  {
    q: 'Can a server (or template) run more than one scenario?',
    a: `No — this is a game limit, not an app limit. A Reforger server config holds exactly
       one scenarioId and the game has no built-in scenario rotation. To offer several
       scenarios, save one template per scenario and either run multiple instances or
       switch a single instance between templates (restart required).`,
  },
  {
    q: 'Can I start from a terrain (map) instead of a scenario?',
    a: `Yes — search terrains as well as scenarios on the wizard's Scenario step; each search
       result is tagged so you can tell them apart. A terrain usually publishes playable
       scenarios of its own, and often several: the Visingsö map ships both a Conflict and a
       Game Master scenario. Pick the terrain and the wizard lists what it provides, so you
       choose which one this server runs (its mod and dependencies come along automatically).
       To run another of that terrain's scenarios, save a second template.`,
  },
  {
    q: 'Why does my instance say "starting…" for several minutes?',
    a: `Because it is. Starting the container is instant, but the Arma server then downloads
       its mods and loads the world before anyone can join — on a heavy modset that is
       minutes, not seconds. The status stays amber ("Starting server…") until the server's
       own log says it is up (it registers with the Reforger backend and enters the online
       game state), and only then turns green ("Started and online"). If it never goes
       green, read the server log on the instance page — a missing mod or a bad config
       shows up there.`,
  },
  {
    q: "Players can't find or join my server. What should I check?",
    a: `Three usual suspects: (1) UDP ports — the game port and A2S port of each running
       instance must be forwarded through your router/firewall to the Docker host
       (defaults: game 2001–2020, A2S 17777–17796); (2) PUBLIC_ADDRESS in .env should be
       your public IP so the server advertises correctly; (3) the template's "Public
       (server browser)" switch must be on for the server to be listed.`,
  },
  {
    q: 'Why did Max players change when I picked a scenario?',
    a: `Because the scenario says how many players it is built for, and the wizard now
       matches it — a 12-player co-op scenario would otherwise be hosted with 64 slots.
       You are told when it happens, and Max players stays fully editable on the Settings
       step: type whatever you want and it is kept. The scenario's own number is shown
       underneath, one click away if you want it back.`,
  },
  {
    q: 'Can I run this on Windows?',
    a: `Yes — on Windows 10/11 with Docker Desktop (WSL2 backend). The README's "Running on
       Windows" section has a short PowerShell block that downloads an installer script and
       runs it. It installs WSL2 (rebooting and resuming by itself if it has to) and Docker
       Desktop, writes the config, opens the firewall for the game and A2S ports, and puts a
       shortcut on your Desktop. The one manual step: the first time Docker Desktop opens it
       shows a Sign in screen — click Skip, you do not need a Docker account, and its engine
       will not start until that screen is dismissed. Read the downloaded script before
       running it — it is downloaded to a file on purpose rather than piped into the shell,
       because that pipe-into-shell pattern is what malware uses and Defender blocks it. Do
       not install Docker Engine inside a WSL distro instead: its NAT only forwards TCP, so
       the UDP game ports never reach the Windows host and players cannot join.`,
  },
  {
    q: 'How do I uninstall it on Windows (or start over after a failed install)?',
    a: `Run uninstall.ps1 from the install folder (%USERPROFILE%\\ReforgerServerManager) — or
       download it from the repo and run it from anywhere if that folder is gone. It shows
       exactly what it found and only proceeds when you type REMOVE. It takes away the
       containers, the install folder, the Desktop shortcut, the firewall rule and the
       installer's leftovers. Your data — templates, instances, saved games and the ~10 GB of
       server files — is KEPT by default, in Docker volumes, so a reinstall picks it up again;
       pass -RemoveData to wipe it for good. Docker Desktop and WSL2 are never touched, since
       other things on your machine may use them.`,
  },
  {
    q: 'Which exact firewall command do I need?',
    a: `Open the "Ports & firewall" panel on Server Instances — it prints the ready-to-run
       PowerShell (Windows) or ufw (Linux) command for the port ranges this install
       actually uses, so you can copy it straight into a terminal. Your router needs the
       same UDP ranges forwarded to this machine's LAN IP.`,
  },
  {
    q: 'I changed the template’s mods, restarted — and the server still runs the old ones.',
    a: `The server downloads and "bakes" its addons once and then reuses that copy. Open the
       instance, stop it, and use the "Stored data" card to clear Downloaded & baked mods:
       the next start fetches and bakes the template's current mod list from scratch (give it
       a few minutes). The same card clears saved game data — which resets the persistent
       world, so only do that when you want a clean slate — and old logs. Changing a
       template's launch parameters no longer needs any of this: the instance rebuilds its
       container by itself on the next start.`,
  },
  {
    q: 'Do mods update automatically?',
    a: `Yes, by default. Mods follow the latest Workshop release: the server checks and
       downloads mod updates when it starts. If an update breaks things, edit the
       template and lock the mod to a known-good version on the Mods step — only locked
       versions are written into config.json.`,
  },
  {
    q: 'What is the difference between Stable and Experimental?',
    a: `They are two separate builds of the dedicated server with separate ~10 GB
       downloads (Steam app 1874900 vs 1890870). Experimental servers only accept
       Experimental game clients. You can run both side by side; each instance is bound
       to one branch when you create it.`,
  },
  {
    q: 'How many instances can I run?',
    a: `Each running instance leases one game port and one A2S port from the ranges in
       .env — 20 each by default. In practice CPU and RAM run out first: plan roughly two
       to four cores and 6–12 GB of RAM per populated server depending on scenario, mods
       and player count.`,
  },
  {
    q: 'Will I lose my save game if I switch an instance to another template?',
    a: `Persistent saves are tied to the template's persistence settings (the hive id).
       When you swap an instance to a template that writes to a different save — or has
       persistence off — the app warns you before applying. Keeping the same hive id
       keeps the same save.`,
  },
  {
    q: 'How do I become admin in-game?',
    a: `Set an Admin password on the template's Settings step, then in the in-game chat
       type #login followed by that password.`,
  },
  {
    q: 'How do I update the server files when the game updates?',
    a: `Open Server Instances and scroll to the Server files section. Use "Check for
       update" to compare your install against Steam, then re-run the download for that
       branch. Restart instances afterwards so they pick up the new build.`,
  },
  {
    q: 'How do I update this manager itself?',
    a: `From the folder with your docker-compose.yaml run: docker compose pull, then
       docker compose up -d. On Windows run .\\start.ps1 -Update from the install folder
       instead. Templates, instances and downloaded server files survive updates — they
       live in ./data and ./serverfiles next to the compose file (Docker named volumes on
       Windows).`,
  },
  {
    q: 'Why is the web GUI only reachable on localhost?',
    a: `By default the GUI binds to 127.0.0.1 on port 7780 as a safety measure — it
       controls the Docker socket, which is root-equivalent on the host. Reach it through
       an SSH tunnel, or put a TLS reverse proxy (nginx, Caddy, …) in front of it for
       remote use. Never forward port 7780 or the RCON ports to the internet.`,
  },
  {
    q: 'Where is everything stored?',
    a: `Next to your docker-compose.yaml: ./data holds the manager database (templates,
       instances) and per-instance configs, profiles and logs; ./serverfiles holds the
       downloaded game server per branch. A server's persistent save lives with its profile,
       at ./data/instances/<id>/profile/.save/game — on the host, never inside a container
       image, so it survives rebuilds and updates. Back up the data folder to keep your setup. On
       Windows the same content lives in Docker named volumes (reforger-data,
       reforger-serverfiles-*) — browse them in Docker Desktop → Volumes — because the
       Linux-owned server files are far faster and permission-clean there than in an
       Explorer folder.`,
  },
]
</script>

<template>
  <div class="container">
    <h1 class="h3 mb-1">User Guide</h1>
    <p class="text-secondary">
      How to go from a fresh install to a running Arma Reforger server — plus answers to
      common questions.
    </p>

    <!-- mini table of contents -->
    <div class="d-flex flex-wrap gap-2 mb-4">
      <a class="btn btn-sm btn-outline-secondary" href="#getting-started">Getting started</a>
      <a class="btn btn-sm btn-outline-secondary" href="#templates">Server Templates</a>
      <a class="btn btn-sm btn-outline-secondary" href="#instances">Server Instances</a>
      <a class="btn btn-sm btn-outline-secondary" href="#faq">FAQ</a>
      <a class="btn btn-sm btn-outline-secondary" href="#links">External references</a>
    </div>

    <!-- GETTING STARTED -->
    <div id="getting-started" class="card mb-4">
      <div class="card-body">
        <h2 class="h5">Getting started</h2>
        <p class="text-secondary small mb-3">
          One-time setup, in order. Steps 1–2 download the pieces every server needs;
          steps 3–4 create and start your first server.
        </p>
        <ol class="mb-2">
          <li class="mb-2">
            <strong>Pull the server runtime image.</strong> Go to
            <router-link to="/instances#server-files">Server Instances → Server files</router-link>
            and pull the Docker image instances run from. This happens once.
          </li>
          <li class="mb-2">
            <strong>Download the server files</strong> for the branch you want (Stable for
            normal play) in the same section — roughly 10 GB via SteamCMD, shared by every
            instance of that branch.
          </li>
          <li class="mb-2">
            <strong>Create a Server Template.</strong> On
            <router-link to="/">Server Templates</router-link> click "New template": pick a
            scenario from the Workshop (its mod and all dependencies are added
            automatically), add extra mods if you like, tune the settings and save. The
            live preview shows the exact <code>config.json</code> the server will run.
          </li>
          <li class="mb-2">
            <strong>Create and start a Server Instance.</strong> On
            <router-link to="/instances">Server Instances</router-link> click "New
            instance", pick your template and branch, and start it. Ports are leased
            automatically; live logs and status stream to the instance page.
          </li>
          <li>
            <strong>Open the ports.</strong> For internet players, forward each running
            instance's UDP game port (default range 2001–2020) and A2S port (17777–17796)
            through your router and host firewall, and set <code>PUBLIC_ADDRESS</code> in
            <code>.env</code> to your public IP. The
            <router-link to="/instances">Ports &amp; firewall</router-link> panel on Server
            Instances prints the exact firewall command for this host. Keep RCON
            (19999–20018) and the web GUI (7780) private.
          </li>
        </ol>
      </div>
    </div>

    <!-- TEMPLATES -->
    <div id="templates" class="card mb-4">
      <div class="card-body">
        <h2 class="h5">Server Templates</h2>
        <p class="text-secondary small mb-3">
          A template is a reusable server definition — scenario, mods and settings — that
          renders to the exact <code>config.json</code> the dedicated server reads. Many
          instances can share one template.
        </p>
        <ul class="mb-0">
          <li class="mb-2">
            <strong>Scenario:</strong> search the Workshop and pick one — each server runs
            exactly one scenario. The wizard shows the current pick and asks for
            confirmation before replacing or removing it (the mods it brought in leave
            with it; your own mods are kept).
          </li>
          <li class="mb-2">
            <strong>Mods:</strong> add mods by search or by pasting Workshop ids/URLs —
            several at once, comma-separated, if you like.
            Dependencies (and their dependencies) come along automatically and are removed
            again when nothing needs them. Mods follow the latest Workshop release unless
            you lock a version. The list can be sorted by name or by the order you added
            mods, or rearranged by hand, and exported/imported as JSON to share
            between templates or friends. Badges show each mod's role:
            <em>scenario</em> (provides the scenario), <em>scenario dependency</em>
            (needed for the scenario to work), <em>addon</em> (an extra you chose),
            <em>dependency</em> (pulled in by an addon) and <em>scenario mod</em> (an addon
            that carries its own scenario — only the scenario picked on step 1 actually
            runs, since a server hosts one scenario at a time).
          </li>
          <li class="mb-2">
            <strong>Settings:</strong> server name, passwords, player limit and view
            distances up front; VON, persistence (save games), RCON and engine launch
            parameters under "advanced". Max players is seeded from the player count the
            chosen scenario declares on the Workshop — override it freely, it is yours.
          </li>
          <li>
            <strong>Import/export:</strong> upload an existing <code>config.json</code> to
            pre-fill the whole wizard, or download the rendered one from the Save step —
            handy for moving from a hand-managed server or another tool.
          </li>
        </ul>
      </div>
    </div>

    <!-- INSTANCES -->
    <div id="instances" class="card mb-4">
      <div class="card-body">
        <h2 class="h5">Server Instances</h2>
        <p class="text-secondary small mb-3">
          An instance is a real running server: a template bound to a branch, a set of
          ports and a Docker container that the manager creates and supervises.
        </p>
        <ul class="mb-0">
          <li class="mb-2">
            <strong>Lifecycle:</strong> start, stop and restart from the UI; live server
            logs stream into the instance page, alongside players, FPS, CPU and memory.
            The Connect line shows the address players use — auto-detected from the
            server log unless <code>PUBLIC_ADDRESS</code> is set.
          </li>
          <li class="mb-2">
            <strong>Reliability:</strong> per instance you can toggle auto-restart on
            crash, auto-start after a host/Docker reboot, and scheduled daily restarts at
            fixed times (server local time).
          </li>
          <li class="mb-2">
            <strong>Template changes:</strong> editing a template does not touch running
            servers — restart an instance to apply it. Swapping an instance to a
            different template warns you if the persistent-save target (hive id) would
            change.
          </li>
          <li>
            <strong>Server files:</strong> the shared per-branch install lives at the
            bottom of the Server Instances page — download, check for updates against
            Steam, or delete it there.
          </li>
        </ul>
      </div>
    </div>

    <!-- FAQ -->
    <div id="faq" class="card mb-4">
      <div class="card-body">
        <h2 class="h5 mb-3">FAQ</h2>
        <details v-for="item in faq" :key="item.q" class="rsm-faq border-bottom py-2">
          <summary class="fw-semibold">{{ item.q }}</summary>
          <p class="text-secondary small mt-2 mb-1">{{ item.a }}</p>
        </details>
      </div>
    </div>

    <!-- EXTERNAL LINKS -->
    <div id="links" class="card mb-4">
      <div class="card-body">
        <h2 class="h5 mb-3">External references</h2>
        <ul class="list-unstyled mb-0">
          <li v-for="l in links" :key="l.href" class="mb-2">
            <a :href="l.href" target="_blank" rel="noopener">{{ l.title }} ↗</a>
            <div class="text-secondary small">{{ l.desc }}</div>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rsm-faq summary {
  cursor: pointer;
  list-style-position: outside;
}
.rsm-faq:last-child {
  border-bottom: 0 !important;
}
</style>
