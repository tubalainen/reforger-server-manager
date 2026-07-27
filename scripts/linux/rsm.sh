#!/bin/sh
# Reforger Server Manager — control command.
#
# Installed to /usr/local/bin/rsm by the Linux installers. Wraps the docker
# compose invocation so day-to-day operation does not depend on remembering
# which compose file this host was set up with.
set -eu

CONF=/etc/reforger-server-manager.conf

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
  rsm update         pull the newest images and restart
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
