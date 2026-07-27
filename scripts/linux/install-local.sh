#!/bin/sh
# Reforger Server Manager — installer for a LOCAL Linux box (home server / LAN).
#
#   curl -fsSL https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/linux/install-local.sh | sudo sh
#
# Prefer to read it first (recommended for anything run as root):
#   curl -fsSLO https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/linux/install-local.sh
#   less install-local.sh && sudo sh install-local.sh
#
# For a PUBLIC cloud server use install-vps.sh instead: it puts HTTPS in front
# of the GUI, which this script deliberately does not.
#
# Non-interactive use (CI, re-runs):
#   RSM_ASSUME_YES=1 RSM_BIND=lan RSM_FIREWALL=1 sh install-local.sh
set -eu

REPO=tubalainen/reforger-server-manager
REF="${RSM_REF:-main}"
RAW="https://raw.githubusercontent.com/$REPO/$REF"
INSTALL_DIR="${RSM_DIR:-/opt/reforger-server-manager}"
CONF=/etc/reforger-server-manager.conf
COMPOSE_FILE=docker-compose.yaml

ASSUME_YES="${RSM_ASSUME_YES:-0}"

# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #
if [ -t 1 ] && command -v tput >/dev/null 2>&1 && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
    B=$(tput bold); G=$(tput setaf 2); Y=$(tput setaf 3); R=$(tput setaf 1); N=$(tput sgr0)
else
    B=''; G=''; Y=''; R=''; N=''
fi
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==>%s %s%s%s\n' "$G" "$N" "$B" "$*" "$N"; }
warn() { printf '%s !  %s%s\n' "$Y" "$*" "$N"; }
die()  { printf '%s ✗  %s%s\n' "$R" "$*" "$N" >&2; exit 1; }

# Prompts read /dev/tty, not stdin: with `curl … | sh` stdin IS the script, so a
# plain `read` would consume the script itself or hit EOF immediately.
have_tty() { [ -r /dev/tty ]; }

ask() {  # ask "prompt" "default"
    _def="${2:-}"
    if [ "$ASSUME_YES" = "1" ] || ! have_tty; then printf '%s' "$_def"; return; fi
    printf '%s' "$1" > /dev/tty
    read -r _a < /dev/tty || _a=''
    [ -n "$_a" ] || _a="$_def"
    printf '%s' "$_a"
}

confirm() {  # confirm "prompt" "default y|n"
    _d="${2:-n}"
    if [ "$ASSUME_YES" = "1" ] || ! have_tty; then [ "$_d" = "y" ]; return; fi
    _hint='[y/N]'; [ "$_d" = "y" ] && _hint='[Y/n]'
    printf '%s %s ' "$1" "$_hint" > /dev/tty
    read -r _a < /dev/tty || _a=''
    [ -n "$_a" ] || _a="$_d"
    case "$_a" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

rand() {  # rand <length> <charset>
    LC_ALL=C tr -dc "$2" < /dev/urandom 2>/dev/null | head -c "$1" || true
}

printf '\n%s  Reforger Server Manager — local Linux installer%s\n' "$B" "$N"
printf '  https://github.com/%s\n' "$REPO"

# --------------------------------------------------------------------------- #
# 1. Environment checks
# --------------------------------------------------------------------------- #
step "Checking the system"
[ "$(uname -s)" = "Linux" ] || die "This installer is for Linux. On Windows use scripts/windows/install.ps1."
[ "$(id -u)" = "0" ] || die "Please run as root:  curl -fsSL $RAW/scripts/linux/install-local.sh | sudo sh"
for tool in curl tr head; do
    command -v "$tool" >/dev/null 2>&1 || die "Required tool '$tool' is missing. Install it and re-run."
done
say "  OK — Linux, running as root."

# --------------------------------------------------------------------------- #
# 2. Docker
# --------------------------------------------------------------------------- #
step "Checking Docker"
install_docker() {
    say "  Installing Docker via the official get.docker.com script..."
    curl -fsSL https://get.docker.com | sh || die "Docker installation failed."
    command -v systemctl >/dev/null 2>&1 && systemctl enable --now docker >/dev/null 2>&1 || true
}

if command -v docker >/dev/null 2>&1; then
    say "  Docker is installed."
else
    warn "Docker is not installed."
    say "  This will run the official installer:  curl -fsSL https://get.docker.com | sh"
    confirm "  Install Docker now?" y || die "Docker is required. Install it, then re-run this script."
    install_docker
fi

if ! docker compose version >/dev/null 2>&1; then
    warn "The 'docker compose' plugin is missing."
    confirm "  Run the Docker installer to add it?" y || die "The compose plugin is required."
    install_docker
    docker compose version >/dev/null 2>&1 || die "Still no 'docker compose' plugin — install docker-compose-plugin and re-run."
fi
say "  Compose plugin: $(docker compose version 2>/dev/null | head -1)"

if ! docker info >/dev/null 2>&1; then
    command -v systemctl >/dev/null 2>&1 && systemctl start docker >/dev/null 2>&1 || true
    docker info >/dev/null 2>&1 || die "The Docker daemon is not running. Start it and re-run."
fi

# --------------------------------------------------------------------------- #
# 3. Files
# --------------------------------------------------------------------------- #
step "Installing to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/serverfiles/stable" "$INSTALL_DIR/serverfiles/experimental"
fetch() {  # fetch <remote path> <local name>
    curl -fsSL "$RAW/$1" -o "$INSTALL_DIR/$2" || die "Could not download $1 (ref '$REF')."
}
fetch "$COMPOSE_FILE" "$COMPOSE_FILE"
fetch ".env.example" ".env.example"
fetch "scripts/linux/rsm.sh" ".rsm.sh"
say "  Downloaded compose file and templates."

# --------------------------------------------------------------------------- #
# 4. .env — generated once, never clobbered
# --------------------------------------------------------------------------- #
step "Configuring"
ENV_FILE="$INSTALL_DIR/.env"
GEN_PASS=''
if [ -f "$ENV_FILE" ]; then
    say "  Existing .env kept as-is (delete it to start over)."
else
    # Alphanumeric only, ambiguous characters removed. No '$': Docker Compose
    # eats a single one, which would silently break the password (#140).
    GEN_PASS=$(rand 20 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789')
    SECRET=$(rand 64 '0-9a-f')
    [ -n "$GEN_PASS" ] && [ -n "$SECRET" ] || die "Could not generate credentials from /dev/urandom."

    # Ask where the GUI should listen. Loopback is the safe default; a LAN bind
    # is what people want when the box lives in a cupboard.
    BIND_CHOICE="${RSM_BIND:-}"
    if [ -z "$BIND_CHOICE" ]; then
        say ""
        say "  Where should the web GUI be reachable from?"
        say "    1) This machine only (http://localhost:7780)  — most secure"
        say "    2) Anywhere on my home network                — to use it from another PC"
        [ "$(ask '  Choice [1]: ' '1')" = "2" ] && BIND_CHOICE=lan || BIND_CHOICE=local
    fi
    if [ "$BIND_CHOICE" = "lan" ]; then WEB_BIND=0.0.0.0; else WEB_BIND=127.0.0.1; fi

    cp "$INSTALL_DIR/.env.example" "$ENV_FILE"
    # Rewrite in place. '|' as the sed delimiter: the secret is hex and the
    # password alphanumeric, so neither can contain it.
    sed -i \
        -e "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$GEN_PASS|" \
        -e "s|^SESSION_SECRET=.*|SESSION_SECRET=$SECRET|" \
        -e "s|^WEB_BIND=.*|WEB_BIND=$WEB_BIND|" \
        "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    say "  Wrote .env (mode 600) with a generated password and session secret."
fi

# Read back what is actually configured, so the firewall and the summary match
# the file even on a re-run over an existing .env.
env_val() { sed -n "s|^$1=||p" "$ENV_FILE" | head -1; }
WEB_BIND=$(env_val WEB_BIND); WEB_BIND=${WEB_BIND:-127.0.0.1}
WEB_PORT=$(env_val WEB_PORT);  WEB_PORT=${WEB_PORT:-7780}
GAME_RANGE=$(env_val GAME_PORT_RANGE); GAME_RANGE=${GAME_RANGE:-2001-2020}
A2S_RANGE=$(env_val A2S_PORT_RANGE);   A2S_RANGE=${A2S_RANGE:-17777-17796}

# --------------------------------------------------------------------------- #
# 5. Host firewall (players need the UDP ranges)
# --------------------------------------------------------------------------- #
step "Host firewall"
ufw_range() { printf '%s' "$1" | tr '-' ':'; }   # ufw wants LO:HI
if command -v ufw >/dev/null 2>&1; then
    say "  Rules to add (game + server browser, UDP):"
    say "    ufw allow $(ufw_range "$GAME_RANGE")/udp"
    say "    ufw allow $(ufw_range "$A2S_RANGE")/udp"
    [ "$WEB_BIND" = "0.0.0.0" ] && say "    ufw allow $WEB_PORT/tcp   (web GUI on your LAN)"
    if [ "${RSM_FIREWALL:-}" = "1" ] || confirm "  Apply them now?" y; then
        ufw allow "$(ufw_range "$GAME_RANGE")/udp" >/dev/null
        ufw allow "$(ufw_range "$A2S_RANGE")/udp" >/dev/null
        [ "$WEB_BIND" = "0.0.0.0" ] && ufw allow "$WEB_PORT/tcp" >/dev/null
        say "  Applied. (ufw itself is left enabled or disabled as you had it.)"
    else
        say "  Skipped — add them yourself before players try to join."
    fi
else
    warn "ufw not found; no firewall changes made."
    say "  Open these yourself if your host has a firewall:"
    say "    UDP $GAME_RANGE and UDP $A2S_RANGE"
fi

# --------------------------------------------------------------------------- #
# 6. rsm command
# --------------------------------------------------------------------------- #
step "Installing the 'rsm' command"
install -m 755 "$INSTALL_DIR/.rsm.sh" /usr/local/bin/rsm
rm -f "$INSTALL_DIR/.rsm.sh"
printf 'RSM_DIR=%s\nRSM_COMPOSE=%s\n' "$INSTALL_DIR" "$COMPOSE_FILE" > "$CONF"
chmod 644 "$CONF"
say "  /usr/local/bin/rsm  (try: rsm status)"

# --------------------------------------------------------------------------- #
# 7. Start
# --------------------------------------------------------------------------- #
step "Starting"
cd "$INSTALL_DIR"
docker compose -f "$COMPOSE_FILE" pull -q 2>/dev/null || docker compose -f "$COMPOSE_FILE" pull
docker compose -f "$COMPOSE_FILE" up -d

if [ "$WEB_BIND" = "0.0.0.0" ]; then
    LAN_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)
    URL="http://${LAN_IP:-<this-machine-ip>}:$WEB_PORT"
else
    URL="http://localhost:$WEB_PORT"
fi

printf '\n%s  Installed.%s\n\n' "$G$B" "$N"
say "  Web GUI   : $URL"
say "  Username  : admin"
if [ -n "$GEN_PASS" ]; then
    printf '  Password  : %s%s%s\n' "$B$Y" "$GEN_PASS" "$N"
    say "              ^ save this now — it is shown only here (also in $ENV_FILE)."
else
    say "  Password  : unchanged (see $ENV_FILE)"
fi
say ""
say "  Manage it with:  rsm start | stop | status | logs | update | config | uninstall"
say ""
say "  Next, so players on the internet can join:"
say "    * forward UDP $GAME_RANGE and UDP $A2S_RANGE on your ROUTER to this machine,"
say "      and give this machine a fixed/reserved LAN IP."
say "    * never forward the RCON ports or the web GUI port ($WEB_PORT)."
say ""
say "  Then open the GUI and use Server Instances → Server files to download the"
say "  Arma server files before creating your first server."
say ""
