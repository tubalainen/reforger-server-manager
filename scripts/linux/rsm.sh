#!/bin/sh
# Reforger Server Manager — control command for whoever manages this machine.
#
# Installed to /usr/local/bin/rsm by the Linux installers. Wraps the docker
# compose invocation so day-to-day operation does not depend on remembering
# which compose file each install was set up with.
#
# A machine can run several installs — stacks — side by side, one per team
# (#204). Each has its own folder, .env, containers and data; rsm knows them
# from one small file per stack in $REGISTRY. Team admins never need rsm: they
# have their own manager's web GUI. This is the host administrator's tool.
set -eu

REPO=tubalainen/reforger-server-manager
# One <stack>.conf per install: RSM_DIR (its folder) and RSM_COMPOSE (its
# compose file name). Overridable for testing.
REGISTRY=${RSM_REGISTRY:-/etc/reforger-server-manager/stacks}
# Before v0.66.0 a machine had one install, described by this file. It is the
# stack its .env names ('reforger' unless RSM_STACK says otherwise) and moves
# into the registry the first time rsm runs as root.
LEGACY_CONF=${RSM_LEGACY_CONF:-/etc/reforger-server-manager.conf}

die() { echo "rsm: $*" >&2; exit 1; }

# A setting from an .env file, with the quotes and CR a hand edit may leave.
env_value_in() {
    [ -f "$1" ] || return 0
    sed -n "s/^$2=//p" "$1" | tail -1 | tr -d "\"' \r"
}
env_value() { env_value_in .env "$1"; }

# The stack an install folder is: RSM_STACK in its .env, 'reforger' by default.
dir_stack() {
    _s=$(env_value_in "$1/.env" RSM_STACK)
    printf '%s' "${_s:-reforger}"
}

# Same rule as backend/stacks.py: lower-case letters, digits and underscores,
# no dashes (a dash separates the stack from the rest of every name).
valid_stack_name() {
    printf '%s' "$1" | grep -Eq '^[a-z0-9][a-z0-9_]{0,30}$'
}

# --------------------------------------------------------------------------- #
# The stack registry
# --------------------------------------------------------------------------- #
legacy_stack() {
    [ -r "$LEGACY_CONF" ] || return 0
    ( RSM_DIR=''; . "$LEGACY_CONF"; [ -n "$RSM_DIR" ] && dir_stack "$RSM_DIR" )
}

adopt_legacy() {
    [ -r "$LEGACY_CONF" ] || return 0
    _name=$(legacy_stack)
    [ -n "$_name" ] || return 0
    if [ ! -e "$REGISTRY/$_name.conf" ]; then
        mkdir -p "$REGISTRY" 2>/dev/null && cp "$LEGACY_CONF" "$REGISTRY/$_name.conf" 2>/dev/null ||
            return 0  # not root: read it where it is, move it another time
    fi
    rm -f "$LEGACY_CONF" 2>/dev/null || true
}

stack_names() {
    {
        for _f in "$REGISTRY"/*.conf; do
            [ -e "$_f" ] && basename "$_f" .conf
        done
        legacy_stack
        echo
    } | sed '/^$/d' | sort -u
}

stack_conf() {
    if [ -r "$REGISTRY/$1.conf" ]; then
        printf '%s' "$REGISTRY/$1.conf"
    elif [ "$(legacy_stack)" = "$1" ]; then
        printf '%s' "$LEGACY_CONF"
    else
        return 1
    fi
}

stack_dir() {  # the folder of a registered stack
    ( RSM_DIR=''; . "$(stack_conf "$1")"; printf '%s' "$RSM_DIR" )
}

# Load one stack: its folder becomes the working directory.
use_stack() {
    STACK_CONF=$(stack_conf "$1") || die "there is no stack named '$1' (see: rsm stacks)"
    RSM_DIR=''
    RSM_COMPOSE=''
    . "$STACK_CONF"
    [ -d "$RSM_DIR" ] || die "stack '$1': its folder '$RSM_DIR' is missing (from $STACK_CONF)"
    cd "$RSM_DIR"
    STACK=$(dir_stack "$RSM_DIR")
    [ "$STACK" = "$1" ] ||
        echo "rsm: note — $STACK_CONF is listed as '$1' but its .env says RSM_STACK=$STACK" >&2
    RAW="https://raw.githubusercontent.com/$REPO/$(files_ref)"
}

# The stack a command is for, when --stack did not say. One stack: that one.
# Several: refuse to guess — stopping or updating the wrong team's servers is
# not a mistake rsm should make on anyone's behalf.
pick_stack() {
    _names=$(stack_names)
    [ -n "$_names" ] || die "no install found — run one of the Linux installers first"
    if [ "$(printf '%s\n' "$_names" | wc -l)" -eq 1 ]; then
        printf '%s' "$_names"
        return
    fi
    _hint=''
    [ "${ALLOWS_ALL:-0}" = 1 ] && _hint=', or --all'
    die "this machine has several stacks ($(printf '%s ' $_names | sed 's/ $//')); say which with --stack NAME$_hint"
}

dc() { docker compose -f "$RSM_COMPOSE" "$@"; }

# Where a stack's setup files come from: the release it runs. A pinned
# MANAGER_VERSION=vX.Y.Z gets that release's files — a newer compose file can
# need a newer image (v0.65.0's Docker gate lives in the image). 'latest' is
# built from main, so it gets main's. RSM_REF overrides both.
files_ref() {
    if [ -n "${RSM_REF:-}" ]; then printf '%s' "$RSM_REF"; return; fi
    _v=$(env_value MANAGER_VERSION)
    case "$_v" in
        v[0-9]*) printf '%s' "$_v" ;;
        *) printf 'main' ;;
    esac
}

usage() {
    cat <<EOF
Reforger Server Manager — for whoever manages this machine

  rsm start               start the manager (and resume auto-start servers)
  rsm stop                stop the manager and every game server
  rsm restart             stop then start
  rsm status              container status
  rsm logs [name]         follow logs (default: the manager)
  rsm update              pull the newest images, restart, and check the setup files
  rsm check               only check .env / the compose file against the release
  rsm config              edit .env (\$EDITOR, default nano), then apply
  rsm reset-password      forget the password set in the GUI; .env's applies again
  rsm uninstall           remove the containers; asks before deleting any data

  Several installs on this machine (stacks, one per team):
  rsm stacks              list them
  rsm ports               every stack's GUI port and UDP ranges, and any overlap
  rsm add-stack NAME      set up another install next to the others
       [--dir DIR] [--web-port N] [--bind local|lan]
  rsm remove-stack NAME   remove one (asks before deleting its data)

  With more than one stack, name it: rsm --stack NAME <command>.
  start, stop, restart, status, update and check also take --all.
EOF
    _names=$(stack_names)
    if [ -n "$_names" ]; then
        echo
        echo "Stacks here: $(printf '%s ' $_names | sed 's/ $//')"
    fi
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

# Yes/no. RSM_ASSUME_YES=1 answers yes; without a terminal, the default.
confirm() {  # confirm "prompt" y|n
    [ "${RSM_ASSUME_YES:-0}" = "1" ] && return 0
    if ! have_tty; then
        [ "${2:-n}" = "y" ]
        return
    fi
    _hint='[y/N]'
    [ "${2:-n}" = "y" ] && _hint='[Y/n]'
    _a=$(ask_tty "$1 $_hint ")
    [ -n "$_a" ] || _a=${2:-n}
    case "$_a" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

rand() {  # rand <length> <charset>
    LC_ALL=C tr -dc "$2" < /dev/urandom 2>/dev/null | head -c "$1" || true
}

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
    check_docker_engine
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
            echo "  .env is never changed for you. To add them:  rsm config --stack $STACK"
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
            compose_replaced=1
        else
            echo "  Left as-is. Refresh it later with:"
            echo "    curl -fsSL $RAW/$RSM_COMPOSE -o $RSM_DIR/$RSM_COMPOSE"
        fi
    fi

    rm -rf "$tmp"; trap - EXIT
}

# Docker Engine 26 is the first that can mount one folder of a volume, which is
# how each game server mounts its own folder since v0.65.0. An older engine
# would mount the whole volume, so the Docker gate refuses and servers cannot
# start. Unreadable version: say nothing rather than guess.
check_docker_engine() {
    v=$(docker version --format '{{.Server.Version}}' 2>/dev/null) || v=''
    case "${v%%.*}" in
        ''|*[!0-9]*) return 0 ;;
    esac
    if [ "${v%%.*}" -lt 26 ]; then
        echo
        echo "  WARNING: Docker Engine $v is older than 26.0, which this release needs"
        echo "  to start game servers. Update Docker (then run 'rsm restart'):"
        echo "    curl -fsSL https://get.docker.com | sh"
    fi
}

# A new compose file changes nothing until the stack is recreated: a plain
# `up -d` does not rebuild a network whose options changed. `down` first.
apply_compose_change() {
    [ "${compose_replaced:-0}" = "1" ] || return 0
    echo
    echo "  The new compose file takes effect when the stack is recreated."
    if have_tty && [ "$(ask_tty '  Recreate it now (game servers stop briefly; auto-start ones come back)? [y/N] ')" = "y" ]; then
        # The compose file binds its volumes to these folders; they must exist.
        mkdir -p data serverfiles/stable serverfiles/experimental
        dc down && remove_legacy_network && dc up -d
    else
        echo "  Do it later with:  sudo rsm restart --stack $STACK"
    fi
}

# Compose files before v0.65.0 put the manager and a socket proxy on a network
# of their own. The Docker gate needs none, and `down` only removes networks the
# current file names, so that one would linger. `rm` refuses while anything is
# still attached, so this never pulls a network out from under a container.
remove_legacy_network() {
    if [ "$STACK" = "reforger" ] && docker network inspect reforger-docker-api >/dev/null 2>&1; then
        docker network rm reforger-docker-api >/dev/null 2>&1 &&
            echo "  Removed the old reforger-docker-api network (no longer used)."
    fi
    return 0
}

# `rsm` itself only ever arrived with an installer run, so a fix to it reached
# nobody who did not re-install (#167). Refresh it at the END of an update, from
# main (or RSM_REF): it is host tooling that drives every stack, whatever
# version each one runs.
#
# The staging file is created IN /usr/local/bin so the swap is a rename: that is
# atomic and gives the destination a new inode, leaving the inode this very
# script is still being read from untouched. A plain `mv` from /tmp would be a
# cross-device copy INTO the running file and could corrupt this execution.
self_update() {
    dl=$(mktemp) || return 0
    _src="https://raw.githubusercontent.com/$REPO/${RSM_REF:-main}/scripts/linux/rsm.sh"
    if ! curl -fsSL "$_src" -o "$dl" 2>/dev/null || [ ! -s "$dl" ]; then
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

# --------------------------------------------------------------------------- #
# Ports across every stack (#204)
# --------------------------------------------------------------------------- #
# Lines "stack kind lo hi" — kind is web (TCP) or game/a2s/rcon (UDP).
port_table() {
    for _s in $(stack_names); do
        _env="$(stack_dir "$_s")/.env"
        _web=$(env_value_in "$_env" WEB_PORT)
        _game=$(env_value_in "$_env" GAME_PORT_RANGE)
        _a2s=$(env_value_in "$_env" A2S_PORT_RANGE)
        _rcon=$(env_value_in "$_env" RCON_PORT_RANGE)
        _web=${_web:-7780}; _game=${_game:-2001-2020}
        _a2s=${_a2s:-17777-17796}; _rcon=${_rcon:-19999-20018}
        echo "$_s web $_web $_web"
        echo "$_s game ${_game%-*} ${_game#*-}"
        echo "$_s a2s ${_a2s%-*} ${_a2s#*-}"
        echo "$_s rcon ${_rcon%-*} ${_rcon#*-}"
    done
}

# Pairs that overlap: GUI ports among GUI ports (TCP), UDP ranges among UDP
# ranges — one stack's game range must not meet another's A2S range either.
port_overlaps() {
    port_table | awk '
        function r(a, b) { return a == b ? a : a "-" b }
        { s[NR]=$1; k[NR]=$2; lo[NR]=$3+0; hi[NR]=$4+0 }
        END {
            for (i = 1; i <= NR; i++) for (j = i + 1; j <= NR; j++) {
                if ((k[i] == "web") != (k[j] == "web")) continue
                if (lo[i] <= hi[j] && lo[j] <= hi[i])
                    printf "  %s %s %s  overlaps  %s %s %s\n",
                        s[i], k[i], r(lo[i], hi[i]), s[j], k[j], r(lo[j], hi[j])
            }
        }'
}

cmd_ports() {
    [ -n "$(stack_names)" ] || die "no install found — run one of the Linux installers first"
    printf '%-12s %-8s %-14s %-14s %s\n' STACK GUI/TCP GAME/UDP A2S/UDP RCON/UDP
    port_table | awk '
        !($1 in order) { order[$1] = ++n; name[n] = $1 }
        { v[$1, $2] = ($3 == $4) ? $3 : $3 "-" $4 }
        END {
            for (i = 1; i <= n; i++) {
                s = name[i]
                printf "%-12s %-8s %-14s %-14s %s\n", s, v[s,"web"], v[s,"game"], v[s,"a2s"], v[s,"rcon"]
            }
        }'
    _clash=$(port_overlaps)
    echo
    if [ -n "$_clash" ]; then
        echo "Overlapping ports — two stacks would fight over these:"
        printf '%s\n' "$_clash"
        echo "Give one of them other ports in its .env (rsm config --stack NAME)."
        return 1
    fi
    echo "No overlaps."
}

# The next free block of `size` UDP ports at or after `start` that meets no
# range any stack already has, as "lo-hi".
next_udp_block() {  # next_udp_block start size
    port_table | awk -v start="$1" -v size="$2" '
        $2 != "web" { n++; lo[n]=$3+0; hi[n]=$4+0 }
        END {
            a = start + 0
            do {
                moved = 0
                for (i = 1; i <= n; i++)
                    if (a <= hi[i] && lo[i] <= a + size - 1) { a = hi[i] + 1; moved = 1 }
            } while (moved)
            if (a + size - 1 > 65535) exit 1
            printf "%d-%d", a, a + size - 1
        }'
}

next_web_port() {
    port_table | awk '
        $2 == "web" { used[$3+0] = 1 }
        END { p = 7780; while (p in used) p++; print p }'
}

# --------------------------------------------------------------------------- #
# Commands about every stack
# --------------------------------------------------------------------------- #
cmd_stacks() {
    _names=$(stack_names)
    [ -n "$_names" ] || die "no install found — run one of the Linux installers first"
    printf '%-12s %-8s %-10s %-5s %s\n' STACK GUI MANAGER GATE FOLDER
    for _s in $_names; do
        _dir=$(stack_dir "$_s")
        _web=$(env_value_in "$_dir/.env" WEB_PORT)
        _state=$(docker inspect -f '{{.State.Status}}' "$_s-manager" 2>/dev/null) || _state=absent
        if docker inspect "$_s-docker-gate" >/dev/null 2>&1; then _gate=yes; else _gate=no; fi
        printf '%-12s %-8s %-10s %-5s %s\n' "$_s" "${_web:-7780}" "$_state" "$_gate" "$_dir"
    done
}

cmd_add_stack() {
    _name=${1:-}
    [ -n "$_name" ] || die "usage: rsm add-stack NAME [--dir DIR] [--web-port N] [--bind local|lan]"
    shift
    valid_stack_name "$_name" ||
        die "'$_name' is not a valid stack name: use 1-31 lower-case letters, digits and underscores, starting with a letter or digit, no dashes (e.g. team2)"
    _dir=/opt/rsm-$_name; _web=''; _bind=''
    while [ $# -gt 0 ]; do
        case "$1" in
            --dir) [ $# -ge 2 ] || die "--dir needs a folder"; _dir=$2; shift 2 ;;
            --web-port) [ $# -ge 2 ] || die "--web-port needs a number"; _web=$2; shift 2 ;;
            --bind) [ $# -ge 2 ] || die "--bind needs local or lan"; _bind=$2; shift 2 ;;
            *) die "add-stack: unknown option '$1'" ;;
        esac
    done
    [ "$(id -u)" = "0" ] || die "add-stack needs root: sudo rsm add-stack $_name"
    if stack_conf "$_name" >/dev/null 2>&1; then die "there already is a stack named '$_name'"; fi
    if [ -e "$_dir" ] && [ -n "$(ls -A "$_dir" 2>/dev/null)" ]; then
        die "$_dir already exists and is not empty; choose another with --dir"
    fi

    # Every stack already here must run the Docker gate (v0.65.0+). An older
    # manager does not know about stacks and would take the new one's servers
    # for its own.
    for _s in $(stack_names); do
        docker inspect "$_s-docker-gate" >/dev/null 2>&1 ||
            die "stack '$_s' still runs a compose file older than v0.65.0 (no $_s-docker-gate container). Update it first: sudo rsm update --stack $_s"
    done
    check_docker_engine

    # Ports: the next free GUI port, and UDP blocks the size of the defaults.
    [ -n "$_web" ] || _web=$(next_web_port)
    case "$_web" in ''|*[!0-9]*) die "--web-port needs a number" ;; esac
    if ! port_table | awk -v w="$_web" '$2 == "web" && $3 + 0 == w + 0 { found = 1 } END { exit found }'; then
        die "GUI port $_web is already another stack's"
    fi
    _game=$(next_udp_block 2001 20) || die "no free block of 20 UDP ports for the game range"
    _a2s=$(next_udp_block 17777 20) || die "no free block of 20 UDP ports for the A2S range"
    _rcon=$(next_udp_block 19999 20) || die "no free block of 20 UDP ports for the RCON range"

    # How the first install is reached is how this one is, unless --bind says.
    _first=$(stack_names | head -1)
    _first_env=''
    [ -z "$_first" ] || _first_env="$(stack_dir "$_first")/.env"
    if [ -z "$_bind" ]; then
        if [ "$(env_value_in "$_first_env" WEB_BIND)" = "0.0.0.0" ]; then _bind=lan; else _bind=local; fi
    fi
    case "$_bind" in
        lan) _web_bind=0.0.0.0 ;;
        local) _web_bind=127.0.0.1 ;;
        *) die "--bind is local or lan" ;;
    esac
    _public=$(env_value_in "$_first_env" PUBLIC_ADDRESS)
    _tz=$(env_value_in "$_first_env" TZ)

    # A VPS install sits behind its own Caddy on ports 80 and 443. A second stack
    # has no HTTPS front of its own yet (#204, phase 4): its GUI must stay on this
    # machine, reached through an SSH tunnel, never plain HTTP on the internet.
    for _s in $(stack_names); do
        if [ "$( RSM_COMPOSE=''; . "$(stack_conf "$_s")"; printf '%s' "$RSM_COMPOSE" )" = docker-compose.vps.yaml ]; then
            echo "Note: this machine runs the VPS setup. The new stack gets no HTTPS front of"
            echo "its own yet, so its GUI is reachable only from this machine (an SSH tunnel)."
            [ "$_bind" = local ] || die "--bind lan would put its GUI on the internet without HTTPS; use --bind local"
            confirm "Set it up anyway?" n || { echo "Cancelled."; return 0; }
            break
        fi
    done

    echo "Setting up stack '$_name' in $_dir"
    echo "  GUI port $_web (TCP); game $_game, A2S $_a2s, RCON $_rcon (UDP)"
    confirm "Go ahead?" y || { echo "Cancelled."; return 0; }

    _raw="https://raw.githubusercontent.com/$REPO/${RSM_REF:-main}"
    mkdir -p "$_dir/data" "$_dir/serverfiles/stable" "$_dir/serverfiles/experimental"
    curl -fsSL "$_raw/docker-compose.yaml" -o "$_dir/docker-compose.yaml" ||
        die "could not download the compose file"
    curl -fsSL "$_raw/.env.example" -o "$_dir/.env.example" ||
        die "could not download .env.example"

    # Alphanumeric only: no '$', which Docker Compose would eat (#140).
    _pass=$(rand 20 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789')
    _secret=$(rand 64 '0-9a-f')
    [ -n "$_pass" ] && [ -n "$_secret" ] || die "could not generate credentials"
    cp "$_dir/.env.example" "$_dir/.env"
    sed -i \
        -e "s|^RSM_STACK=.*|RSM_STACK=$_name|" \
        -e "s|^WEB_BIND=.*|WEB_BIND=$_web_bind|" \
        -e "s|^WEB_PORT=.*|WEB_PORT=$_web|" \
        -e "s|^GAME_PORT_RANGE=.*|GAME_PORT_RANGE=$_game|" \
        -e "s|^A2S_PORT_RANGE=.*|A2S_PORT_RANGE=$_a2s|" \
        -e "s|^RCON_PORT_RANGE=.*|RCON_PORT_RANGE=$_rcon|" \
        -e "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$_pass|" \
        -e "s|^SESSION_SECRET=.*|SESSION_SECRET=$_secret|" \
        "$_dir/.env"
    [ -z "$_public" ] || sed -i "s|^PUBLIC_ADDRESS=.*|PUBLIC_ADDRESS=$_public|" "$_dir/.env"
    [ -z "$_tz" ] || sed -i "s|^TZ=.*|TZ=$_tz|" "$_dir/.env"
    printf '\n# Keeps Docker Compose from mixing this install up with another (#204).\nCOMPOSE_PROJECT_NAME=%s\n' \
        "$_name" >> "$_dir/.env"
    chmod 600 "$_dir/.env"

    mkdir -p "$REGISTRY"
    printf 'RSM_DIR=%s\nRSM_COMPOSE=%s\n' "$_dir" docker-compose.yaml > "$REGISTRY/$_name.conf"
    chmod 644 "$REGISTRY/$_name.conf"

    if command -v ufw >/dev/null 2>&1; then
        _g=$(printf '%s' "$_game" | tr - :); _q=$(printf '%s' "$_a2s" | tr - :)
        echo "Firewall rules for its players (UDP):"
        echo "  ufw allow $_g/udp"
        echo "  ufw allow $_q/udp"
        [ "$_bind" != lan ] || echo "  ufw allow $_web/tcp   (its web GUI on your LAN)"
        if confirm "Apply them now?" y; then
            ufw allow "$_g/udp" >/dev/null
            ufw allow "$_q/udp" >/dev/null
            [ "$_bind" != lan ] || ufw allow "$_web/tcp" >/dev/null
        fi
    else
        echo "No ufw here: open UDP $_game and $_a2s in your firewall yourself."
    fi

    ( cd "$_dir" && { docker compose pull -q 2>/dev/null || docker compose pull; } &&
      docker compose up -d )

    if [ "$_bind" = lan ]; then
        _ip=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)
        _url="http://${_ip:-<this-machine-ip>}:$_web"
    else
        _url="http://localhost:$_web"
    fi
    echo
    echo "Stack '$_name' is up."
    echo "  Web GUI  : $_url"
    echo "  Username : admin"
    echo "  Password : $_pass"
    echo "             ^ give this to the team; they can change it under System > Account."
    echo "  Players  : forward UDP $_game and $_a2s on your router to this machine."
    echo "  Manage it with: rsm <command> --stack $_name"
}

cmd_remove_stack() {
    _name=${1:-}
    [ -n "$_name" ] || die "usage: rsm remove-stack NAME"
    use_stack "$_name"
    [ "$(id -u)" = "0" ] || die "remove-stack needs root: sudo rsm remove-stack $_name"
    echo "This stops stack '$_name' and removes its containers."
    confirm "Continue?" n || { echo "Cancelled."; return 0; }
    dc down --remove-orphans || true
    _game=$(env_value GAME_PORT_RANGE)
    _a2s=$(env_value A2S_PORT_RANGE)
    echo
    echo "Its data is still in $RSM_DIR (templates, saves, server files)."
    if confirm "Delete that too? This CANNOT be undone." n; then
        cd /
        docker volume rm "$STACK-data" "$STACK-serverfiles-stable" \
            "$STACK-serverfiles-experimental" "$STACK-docker-gate" >/dev/null 2>&1 || true
        rm -rf "$RSM_DIR"
        echo "Removed $RSM_DIR."
    else
        echo "Kept $RSM_DIR."
    fi
    rm -f "$STACK_CONF"
    echo "Stack '$_name' is no longer known to rsm."
    if command -v ufw >/dev/null 2>&1 && [ -n "$_game" ]; then
        echo "Its firewall rules, if you added them, go with:"
        echo "  ufw delete allow $(printf '%s' "$_game" | tr - :)/udp"
        echo "  ufw delete allow $(printf '%s' "$_a2s" | tr - :)/udp"
    fi
    if [ -z "$(stack_names)" ]; then
        rm -f /usr/local/bin/rsm
        rmdir "$REGISTRY" 2>/dev/null || true
        echo "That was the last stack: the rsm command was removed too."
    fi
}

# --------------------------------------------------------------------------- #
# Commands for one stack
# --------------------------------------------------------------------------- #
stack_cmd() {
    _cmd=$1
    shift
    case "$_cmd" in
        start)   dc up -d ;;
        stop)    dc down ;;
        restart) dc down && dc up -d ;;
        status)  dc ps ;;
        logs)
            # Default to the manager; the game servers are separate containers and
            # are read through the GUI (or `docker logs <stack>-instance-<id>`).
            if [ $# -eq 0 ]; then dc logs -f --tail 200 manager; else dc logs -f --tail 200 "$@"; fi
            ;;
        update)
            dc pull
            dc up -d
            echo "Updated. Current images:"
            dc images
            echo
            echo "Checking the setup files against this release..."
            check_setup_files
            apply_compose_change
            ;;
        check)
            echo "Checking the setup files against this release..."
            check_setup_files
            apply_compose_change
            ;;
        config)
            "${EDITOR:-nano}" .env
            echo "Applying..."
            dc up -d
            ;;
        reset-password)
            # A team admin who forgot the password they set in the GUI (#204): the
            # .env password (ADMIN_PASSWORD) becomes the one in effect again.
            docker exec -u app "$STACK-manager" python manage.py reset-password
            ;;
        uninstall)
            if [ "$(stack_names | wc -l)" -gt 1 ]; then
                cmd_remove_stack "$STACK"
                return
            fi
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
                rm -f "$STACK_CONF" /usr/local/bin/rsm
                rmdir "$REGISTRY" 2>/dev/null || true
                echo "Removed $RSM_DIR and the rsm command."
            else
                echo "Kept $RSM_DIR. Re-run an installer to use it again."
                echo "Remove the command itself with: rm -f /usr/local/bin/rsm $STACK_CONF"
            fi
            ;;
    esac
}

# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
adopt_legacy

# --stack NAME, --stack=NAME and --all may stand anywhere on the line.
STACK_ARG=''
ALL=0
_rest=''
_want_stack=0
quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
for _a in "$@"; do
    if [ "$_want_stack" = 1 ]; then
        STACK_ARG=$_a
        _want_stack=0
        continue
    fi
    case "$_a" in
        --stack) _want_stack=1 ;;
        --stack=*) STACK_ARG=${_a#--stack=} ;;
        --all) ALL=1 ;;
        *) _rest="$_rest $(quote "$_a")" ;;
    esac
done
[ "$_want_stack" = 0 ] || die "--stack needs a stack name"
eval "set -- $_rest"
CMD=${1:-}
[ $# -eq 0 ] || shift

case "$CMD" in
    ""|-h|--help|help) usage; exit 0 ;;
    stacks) cmd_stacks; exit 0 ;;
    ports) cmd_ports; exit $? ;;
    add-stack) cmd_add_stack "$@"; exit 0 ;;
    remove-stack) cmd_remove_stack "$@"; exit 0 ;;
    start|stop|restart|status|update|check) ALLOWS_ALL=1 ;;
    logs|config|reset-password|uninstall)
        [ "$ALL" = 0 ] || die "'$CMD' works on one stack at a time: use --stack NAME" ;;
    *)
        echo "rsm: unknown command '$CMD'" >&2
        echo >&2
        usage >&2
        exit 1
        ;;
esac

if [ "$ALL" = 1 ]; then
    [ -z "$STACK_ARG" ] || die "--all and --stack do not go together"
    _failed=''
    for _s in $(stack_names); do
        echo "== stack $_s"
        ( use_stack "$_s" && stack_cmd "$CMD" "$@" ) || _failed="$_failed $_s"
        echo
    done
    [ "$CMD" != update ] || self_update
    [ -z "$_failed" ] || die "failed for:$_failed"
    exit 0
fi

if [ -z "$STACK_ARG" ]; then STACK_ARG=$(pick_stack) || exit 1; fi
use_stack "$STACK_ARG"
stack_cmd "$CMD" "$@"
[ "$CMD" != update ] || self_update
exit 0
