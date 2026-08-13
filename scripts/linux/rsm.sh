#!/bin/sh
# Reforger Server Manager — control command.
#
# Installed to /usr/local/bin/rsm by the Linux installers. Wraps the docker
# compose invocation so day-to-day operation does not depend on remembering
# which compose file this host was set up with.
set -eu

CONF=/etc/reforger-server-manager.conf
REPO=tubalainen/reforger-server-manager
RAW="https://raw.githubusercontent.com/$REPO/${RSM_REF:-main}"

if [ ! -r "$CONF" ]; then
    echo "rsm: $CONF not found — is Reforger Server Manager installed?" >&2
    exit 1
fi
# Written by the installer: RSM_DIR (install folder) and RSM_COMPOSE (filename).
. "$CONF"

if [ ! -d "${RSM_DIR:-}" ]; then
    echo "rsm: install directory '${RSM_DIR:-}' is missing (from $CONF)" >&2
    exit 1
fi
cd "$RSM_DIR"

dc() { docker compose -f "$RSM_COMPOSE" "$@"; }

usage() {
    cat <<EOF
Reforger Server Manager

  rsm start          start the manager (and resume auto-start servers)
  rsm stop           stop the manager and every game server
  rsm restart        stop then start
  rsm status         container status
  rsm logs [name]    follow logs (default: the manager)
  rsm update         pull the newest images, restart, and check your setup files
  rsm check          only check .env / the compose file against this release
  rsm config         edit .env (\$EDITOR, default nano), then apply
  rsm uninstall      remove containers; asks before deleting any data

Install folder : $RSM_DIR
Compose file   : $RSM_COMPOSE
EOF
}

# Prompts must read the terminal directly: rsm may itself be run from a pipe.
ask_tty() {
    printf '%s' "$1" > /dev/tty
    read -r _reply < /dev/tty || _reply=""
    printf '%s' "$_reply"
}

# Must OPEN /dev/tty, not just stat it: it exists but is unopenable in a cron
# job or a detached shell, where `[ -r /dev/tty ]` still says yes. The open runs
# in a SUBSHELL on purpose — a redirection failure on a special builtin (`:`,
# `exec`) exits a POSIX shell outright, and here that would abort the update.
have_tty() { (exec < /dev/tty) 2>/dev/null; }

# --------------------------------------------------------------------------- #
# Setup-file check (#167)
# --------------------------------------------------------------------------- #
# `docker compose pull` updates the IMAGE. It cannot update the two files that
# live here: .env (yours — it holds your password) and the compose file (the
# project's, but downloaded once by the installer). So a release that adds an
# .env setting or rewires the compose file reached nobody who did not read the
# notes carefully. This says it out loud, on the machine, at update time:
#   * .env  — never touched, only reported: new settings are listed for you.
#   * compose file — diffed against the current release; refreshing is offered,
#     and the old file is kept as a .bak first.
check_setup_files() {
    tmp=$(mktemp -d) || return 0
    trap 'rm -rf "$tmp"' EXIT

    if ! curl -fsSL "$RAW/.env.example" -o "$tmp/env.example" 2>/dev/null; then
        echo "  (could not reach GitHub to check your .env — skipping that check)"
        rm -rf "$tmp"; trap - EXIT; return 0
    fi

    # Keys assigned in the shipped example that your .env does not assign at all.
    keys_of() { sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' "$1" | sort -u; }
    if [ -f .env ]; then
        keys_of "$tmp/env.example" > "$tmp/theirs"
        keys_of .env > "$tmp/mine"
        new_keys=$(comm -23 "$tmp/theirs" "$tmp/mine")
        if [ -n "$new_keys" ]; then
            echo
            echo "  This release knows settings your .env does not have:"
            shown=0
            for k in $new_keys; do
                shown=$((shown + 1))
                [ "$shown" -le 12 ] && sed -n "s/^\($k=.*\)/    \1/p" "$tmp/env.example" | head -1
            done
            [ "$shown" -gt 12 ] && echo "    ... and $((shown - 12)) more"
            echo
            echo "  They are optional unless the release notes say otherwise, and your"
            echo "  .env is never changed for you. To add them:  rsm config"
            echo "  Full file with comments: $RAW/.env.example"
        fi
    fi

    # The compose file: offer a refresh only when it actually differs.
    if curl -fsSL "$RAW/$RSM_COMPOSE" -o "$tmp/compose" 2>/dev/null &&
       ! cmp -s "$tmp/compose" "$RSM_COMPOSE"; then
        echo
        echo "  Your $RSM_COMPOSE differs from this release's:"
        if command -v diff >/dev/null 2>&1; then
            diff -u "$RSM_COMPOSE" "$tmp/compose" | sed -n '3,25p' | sed 's/^/    /'
        fi
        if have_tty && [ "$(ask_tty '  Replace it (a backup is kept)? [y/N] ')" = "y" ]; then
            backup="$RSM_COMPOSE.bak-$(date +%Y%m%d%H%M%S)"
            cp "$RSM_COMPOSE" "$backup"
            cp "$tmp/compose" "$RSM_COMPOSE"
            echo "  Replaced. Previous file kept as $backup"
        else
            echo "  Left as-is. Refresh it later with:"
            echo "    curl -fsSL $RAW/$RSM_COMPOSE -o $RSM_DIR/$RSM_COMPOSE"
        fi
    fi

    rm -rf "$tmp"; trap - EXIT
}

# `rsm` itself only ever arrived with an installer run, so a fix to it reached
# nobody who did not re-install (#167). Refresh it at the END of an update.
#
# The staging file is created IN /usr/local/bin so the swap is a rename: that is
# atomic and gives the destination a new inode, leaving the inode this very
# script is still being read from untouched. A plain `mv` from /tmp would be a
# cross-device copy INTO the running file and could corrupt this execution.
self_update() {
    dl=$(mktemp) || return 0
    if ! curl -fsSL "$RAW/scripts/linux/rsm.sh" -o "$dl" 2>/dev/null || [ ! -s "$dl" ]; then
        rm -f "$dl"; return 0
    fi
    if cmp -s "$dl" /usr/local/bin/rsm; then
        rm -f "$dl"; return 0
    fi
    if staged=$(mktemp /usr/local/bin/.rsm.XXXXXX 2>/dev/null); then
        cat "$dl" > "$staged" && chmod 755 "$staged" && mv "$staged" /usr/local/bin/rsm &&
            echo "  The 'rsm' command itself was updated (active from the next run)." ||
            { rm -f "$staged"; echo "  Could not update the 'rsm' command itself."; }
    else
        echo "  A newer 'rsm' command is available — re-run 'sudo rsm update' to get it."
    fi
    rm -f "$dl"
}

case "${1:-}" in
    start)   dc up -d ;;
    stop)    dc down ;;
    restart) dc down && dc up -d ;;
    status)  dc ps ;;
    logs)
        shift
        # Default to the manager; the game servers are separate containers and
        # are read through the GUI (or `docker logs reforger-instance-<id>`).
        if [ $# -eq 0 ]; then dc logs -f --tail 200 manager; else dc logs -f --tail 200 "$@"; fi
        ;;
    update)
        dc pull
        dc up -d
        echo "Updated. Current images:"
        dc images
        echo
        echo "Checking your setup files against this release..."
        check_setup_files
        self_update
        ;;
    check)
        echo "Checking your setup files against this release..."
        check_setup_files
        ;;
    config)
        "${EDITOR:-nano}" .env
        echo "Applying..."
        dc up -d
        ;;
    uninstall)
        echo "This removes the Reforger Server Manager containers on this host."
        [ "$(ask_tty 'Continue? [y/N] ')" = "y" ] || { echo "Cancelled."; exit 0; }
        dc down --remove-orphans || true
        echo
        echo "Containers removed. Your data is still in $RSM_DIR:"
        echo "  data/        manager database, server configs, saves and logs"
        echo "  serverfiles/ the downloaded Arma server files (large)"
        echo
        echo "Delete that folder too? This CANNOT be undone and destroys saved games."
        if [ "$(ask_tty 'Delete all data? [y/N] ')" = "y" ]; then
            cd /
            rm -rf "$RSM_DIR"
            rm -f "$CONF" /usr/local/bin/rsm
            echo "Removed $RSM_DIR and the rsm command."
        else
            echo "Kept $RSM_DIR. Re-run an installer to use it again."
            echo "Remove the command itself with: rm -f /usr/local/bin/rsm $CONF"
        fi
        ;;
    ""|-h|--help|help) usage ;;
    *)
        echo "rsm: unknown command '$1'" >&2
        echo >&2
        usage >&2
        exit 1
        ;;
esac
