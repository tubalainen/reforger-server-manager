#!/bin/sh
# Reforger Server Manager — installer for a PUBLIC Linux VPS (HTTPS via Caddy).
#
#   curl -fsSL https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/linux/install-vps.sh | sudo sh
#
# Prefer to read it first (recommended for anything run as root):
#   curl -fsSLO https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/linux/install-vps.sh
#   less install-vps.sh && sudo sh install-vps.sh
#
# Sets up the hardened stack: Caddy terminates TLS and is the only thing
# published, the manager gets no port of its own, and the Docker API sits behind
# a least-privilege proxy on an internal network.
#
# Non-interactive use:
#   RSM_ASSUME_YES=1 RSM_DOMAIN=reforger.example.com RSM_FIREWALL=1 sh install-vps.sh
set -eu

REPO=tubalainen/reforger-server-manager
REF="${RSM_REF:-main}"
RAW="https://raw.githubusercontent.com/$REPO/$REF"
INSTALL_DIR="${RSM_DIR:-/opt/reforger-server-manager}"
CONF=/etc/reforger-server-manager.conf
COMPOSE_FILE=docker-compose.vps.yaml

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

# With `curl … | sh` stdin is the script itself, so prompts must use /dev/tty.
have_tty() { [ -r /dev/tty ]; }

ask() {
    _def="${2:-}"
    if [ "$ASSUME_YES" = "1" ] || ! have_tty; then printf '%s' "$_def"; return; fi
    printf '%s' "$1" > /dev/tty
    read -r _a < /dev/tty || _a=''
    [ -n "$_a" ] || _a="$_def"
    printf '%s' "$_a"
}

confirm() {
    _d="${2:-n}"
    if [ "$ASSUME_YES" = "1" ] || ! have_tty; then [ "$_d" = "y" ]; return; fi
    _hint='[y/N]'; [ "$_d" = "y" ] && _hint='[Y/n]'
    printf '%s %s ' "$1" "$_hint" > /dev/tty
    read -r _a < /dev/tty || _a=''
    [ -n "$_a" ] || _a="$_d"
    case "$_a" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

rand() { LC_ALL=C tr -dc "$2" < /dev/urandom 2>/dev/null | head -c "$1" || true; }

printf '\n%s  Reforger Server Manager — VPS installer (public server, HTTPS)%s\n' "$B" "$N"
printf '  https://github.com/%s\n' "$REPO"

# --------------------------------------------------------------------------- #
# 1. Environment checks
# --------------------------------------------------------------------------- #
step "Checking the system"
[ "$(uname -s)" = "Linux" ] || die "This installer is for Linux."
[ "$(id -u)" = "0" ] || die "Please run as root:  curl -fsSL $RAW/scripts/linux/install-vps.sh | sudo sh"
for tool in curl tr head sed; do
    command -v "$tool" >/dev/null 2>&1 || die "Required tool '$tool' is missing. Install it and re-run."
done
say "  OK — Linux, running as root."

# --------------------------------------------------------------------------- #
# 2. Where the GUI will live
# --------------------------------------------------------------------------- #
step "Web address"
DOMAIN="${RSM_DOMAIN:-}"
if [ -z "$DOMAIN" ]; then
    say "  A domain name pointed at this server gets you a real, automatic HTTPS"
    say "  certificate. Without one the GUI can only be served over plain HTTP,"
    say "  which sends your password across the internet in the clear."
    say ""
    say "  Enter your domain (e.g. reforger.example.com),"
    DOMAIN=$(ask "  or leave blank for plain HTTP on this server's IP: " "")
fi

if [ -n "$DOMAIN" ]; then
    SITE_ADDRESS="$DOMAIN"
    say "  Using https://$DOMAIN"
else
    SITE_ADDRESS=":80"
    warn "No domain given — the GUI will be plain HTTP on this server's IP."
    warn "Your login and session will travel UNENCRYPTED. Testing only."
    confirm "  Continue without HTTPS?" n || die "Aborted. Re-run with a domain name."
fi

# Best-effort DNS sanity check: a domain that does not resolve here is the most
# common reason the first certificate request fails.
if [ -n "$DOMAIN" ]; then
    # Compare per address family. A dual-stack host publishes both A and AAAA,
    # and getent may return either first — comparing a v6 answer against our v4
    # address produced a false "does not match" exactly when someone was already
    # debugging DNS. Let's Encrypt also *prefers* IPv6 when an AAAA exists, so a
    # stale or unrouted AAAA fails validation however correct the A record is.
    PUBLIC_IP=$(curl -fsS -4 --max-time 5 https://api.ipify.org 2>/dev/null || true)
    PUBLIC_IP6=$(curl -fsS -6 --max-time 5 https://api64.ipify.org 2>/dev/null || true)
    RESOLVED=$(getent ahosts "$DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u || true)
    DOMAIN_A=$(printf '%s\n' "$RESOLVED" | grep -v ':' | head -1 || true)
    DOMAIN_AAAA=$(printf '%s\n' "$RESOLVED" | grep ':' | head -1 || true)

    if [ -z "$DOMAIN_A" ] && [ -z "$DOMAIN_AAAA" ]; then
        warn "$DOMAIN does not resolve yet. Create an A record pointing at this server;"
        say  "  Caddy retries automatically once it does."
    else
        [ -n "$DOMAIN_A" ] && say "  A    $DOMAIN → $DOMAIN_A"
        [ -n "$DOMAIN_AAAA" ] && say "  AAAA $DOMAIN → $DOMAIN_AAAA"
        if [ -n "$DOMAIN_A" ] && [ -n "$PUBLIC_IP" ] && [ "$DOMAIN_A" != "$PUBLIC_IP" ]; then
            warn "The A record points at $DOMAIN_A but this server is $PUBLIC_IP."
            say  "  If you just created it, this is normal — it needs to propagate."
        fi
        if [ -n "$DOMAIN_AAAA" ] && [ -z "$PUBLIC_IP6" ]; then
            warn "An AAAA record exists, but this server has no working IPv6."
            say  "  Let's Encrypt PREFERS IPv6 when an AAAA is published, so validation"
            say  "  will fail even though the A record is correct. Remove the AAAA record"
            say  "  (or make IPv6 reachable) before continuing."
        elif [ -n "$DOMAIN_AAAA" ] && [ -n "$PUBLIC_IP6" ] && [ "$DOMAIN_AAAA" != "$PUBLIC_IP6" ]; then
            warn "The AAAA record points at $DOMAIN_AAAA but this server is $PUBLIC_IP6."
            say  "  Let's Encrypt prefers IPv6, so fix or remove the AAAA record."
        fi
    fi
fi

# --------------------------------------------------------------------------- #
# 3. Docker
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
    confirm "  Install Docker now?" y || die "Docker is required."
    install_docker
fi
if ! docker compose version >/dev/null 2>&1; then
    warn "The 'docker compose' plugin is missing."
    confirm "  Run the Docker installer to add it?" y || die "The compose plugin is required."
    install_docker
    docker compose version >/dev/null 2>&1 || die "Still no 'docker compose' plugin."
fi
if ! docker info >/dev/null 2>&1; then
    command -v systemctl >/dev/null 2>&1 && systemctl start docker >/dev/null 2>&1 || true
    docker info >/dev/null 2>&1 || die "The Docker daemon is not running."
fi
say "  Compose plugin: $(docker compose version 2>/dev/null | head -1)"

# --------------------------------------------------------------------------- #
# 4. Files
# --------------------------------------------------------------------------- #
step "Installing to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/serverfiles/stable" "$INSTALL_DIR/serverfiles/experimental"
fetch() { curl -fsSL "$RAW/$1" -o "$INSTALL_DIR/$2" || die "Could not download $1 (ref '$REF')."; }
fetch "$COMPOSE_FILE" "$COMPOSE_FILE"
fetch ".env.example" ".env.example"
fetch "scripts/linux/rsm.sh" ".rsm.sh"
say "  Downloaded compose file and templates."

# --------------------------------------------------------------------------- #
# 5. .env
# --------------------------------------------------------------------------- #
step "Configuring"
ENV_FILE="$INSTALL_DIR/.env"
GEN_PASS=''
if [ -f "$ENV_FILE" ]; then
    say "  Existing .env kept; updating SITE_ADDRESS only."
    if grep -q '^SITE_ADDRESS=' "$ENV_FILE"; then
        sed -i "s|^SITE_ADDRESS=.*|SITE_ADDRESS=$SITE_ADDRESS|" "$ENV_FILE"
    else
        printf 'SITE_ADDRESS=%s\n' "$SITE_ADDRESS" >> "$ENV_FILE"
    fi
else
    GEN_PASS=$(rand 20 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789')
    SECRET=$(rand 64 '0-9a-f')
    [ -n "$GEN_PASS" ] && [ -n "$SECRET" ] || die "Could not generate credentials from /dev/urandom."
    cp "$INSTALL_DIR/.env.example" "$ENV_FILE"
    sed -i \
        -e "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$GEN_PASS|" \
        -e "s|^SESSION_SECRET=.*|SESSION_SECRET=$SECRET|" \
        -e "s|^SITE_ADDRESS=.*|SITE_ADDRESS=$SITE_ADDRESS|" \
        "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    say "  Wrote .env (mode 600) with a generated password and session secret."
fi

env_val() { sed -n "s|^$1=||p" "$ENV_FILE" | head -1; }
GAME_RANGE=$(env_val GAME_PORT_RANGE); GAME_RANGE=${GAME_RANGE:-2001-2020}
A2S_RANGE=$(env_val A2S_PORT_RANGE);   A2S_RANGE=${A2S_RANGE:-17777-17796}

# --------------------------------------------------------------------------- #
# 6. Host firewall — SSH first, always
# --------------------------------------------------------------------------- #
step "Host firewall"
# The port of THIS session is the one that must survive. $SSH_CONNECTION is
# "client_ip client_port server_ip server_port", so field 4 is what we are
# actually connected to — more reliable than parsing sshd_config.
detect_ssh_port() {
    if [ -n "${SSH_CONNECTION:-}" ]; then
        printf '%s' "$SSH_CONNECTION" | awk '{print $4}'
        return
    fi
    _p=$(sed -n 's/^[[:space:]]*Port[[:space:]]\+\([0-9]\+\).*/\1/p' /etc/ssh/sshd_config 2>/dev/null | head -1)
    printf '%s' "${_p:-22}"
}
ufw_range() { printf '%s' "$1" | tr '-' ':'; }

if command -v ufw >/dev/null 2>&1; then
    SSH_PORT=$(detect_ssh_port)
    case "$SSH_PORT" in ''|*[!0-9]*) SSH_PORT=22 ;; esac
    say "  Rules to add:"
    say "    ufw allow $SSH_PORT/tcp                      (SSH — added FIRST)"
    say "    ufw allow 80,443/tcp                       (Caddy: HTTPS + renewal)"
    say "    ufw allow $(ufw_range "$GAME_RANGE")/udp                 (game)"
    say "    ufw allow $(ufw_range "$A2S_RANGE")/udp               (server browser)"
    if ufw status 2>/dev/null | head -1 | grep -qi inactive; then
        say "    ufw enable                                 (currently inactive)"
        NEED_ENABLE=1
    else
        NEED_ENABLE=0
    fi
    say ""
    say "  Note: the web GUI gets NO port of its own — it is reached only through Caddy."
    if [ "${RSM_FIREWALL:-}" = "1" ] || confirm "  Apply these now?" y; then
        # SSH before anything else, and before enabling, so a live session cannot
        # be cut off by the rules that follow.
        ufw allow "$SSH_PORT/tcp" >/dev/null
        ufw allow 80/tcp >/dev/null
        ufw allow 443/tcp >/dev/null
        ufw allow "$(ufw_range "$GAME_RANGE")/udp" >/dev/null
        ufw allow "$(ufw_range "$A2S_RANGE")/udp" >/dev/null
        if [ "$NEED_ENABLE" = "1" ]; then
            ufw --force enable >/dev/null
            say "  Applied and enabled (SSH on $SSH_PORT is allowed)."
        else
            say "  Applied (ufw was already active)."
        fi
    else
        say "  Skipped — apply them yourself before players try to join."
    fi
else
    warn "ufw not found; no firewall changes made. Open these yourself:"
    say  "    TCP 80, 443   and   UDP $GAME_RANGE, UDP $A2S_RANGE"
fi

# --------------------------------------------------------------------------- #
# 7. rsm command
# --------------------------------------------------------------------------- #
step "Installing the 'rsm' command"
install -m 755 "$INSTALL_DIR/.rsm.sh" /usr/local/bin/rsm
rm -f "$INSTALL_DIR/.rsm.sh"
printf 'RSM_DIR=%s\nRSM_COMPOSE=%s\n' "$INSTALL_DIR" "$COMPOSE_FILE" > "$CONF"
chmod 644 "$CONF"
say "  /usr/local/bin/rsm  (try: rsm status)"

# --------------------------------------------------------------------------- #
# 8. Start
# --------------------------------------------------------------------------- #
step "Starting"
cd "$INSTALL_DIR"
docker compose -f "$COMPOSE_FILE" pull -q 2>/dev/null || docker compose -f "$COMPOSE_FILE" pull
docker compose -f "$COMPOSE_FILE" up -d

if [ -n "$DOMAIN" ]; then URL="https://$DOMAIN"; else URL="http://${PUBLIC_IP:-<this-server-ip>}"; fi

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
[ -n "$DOMAIN" ] && say "  Watch the certificate being issued:  rsm logs caddy"
say ""
printf '%s  ONE MORE FIREWALL — your VPS provider has its own%s\n' "$Y$B" "$N"
say "  Hetzner Cloud Firewall, AWS security groups, Oracle Cloud, Google Cloud and"
say "  DigitalOcean filter traffic BEFORE it reaches this machine. Several of them"
say "  block almost everything by default, so the rules above are not enough on"
say "  their own. In your provider's control panel, allow inbound:"
say ""
say "    TCP 80, 443          the web GUI (and certificate renewal)"
say "    UDP $GAME_RANGE         players joining"
say "    UDP $A2S_RANGE       the in-game server browser"
say ""
say "  If the GUI or your server never becomes reachable, this is almost always why."
say ""
say "  Then open the GUI and use Server Instances → Server files to download the"
say "  Arma server files before creating your first server."
say ""
