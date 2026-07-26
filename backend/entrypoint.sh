#!/bin/sh
# Started as root so first-run setup needs no manual host commands,
# then drops privileges to the unprivileged 'app' user.
set -e

if [ "$(id -u)" = "0" ]; then
    # ./data is auto-created root-owned by the docker bind mount on first
    # run; hand it to the app user so SQLite can create its database.
    # -R (recursive) is required, not just the top directory: an older image
    # ran as root and left the database and per-instance config folders
    # root-owned, so after upgrading to the unprivileged runtime the app user
    # hit "readonly database" and "Permission denied .../configs/server.json"
    # on every write (#140). Re-assert ownership of the whole tree each start.
    # A Windows-hosted bind (drvfs/9p) rejects chown but is already
    # world-writable, so a failure here is not fatal.
    chown -R app:app /data 2>/dev/null || \
        echo "NOTE: could not chown /data (expected on a Windows bind mount)" >&2

    # Grant the app user access to the docker socket, whatever GID the
    # host uses for it.
    if [ -S /var/run/docker.sock ]; then
        DOCKER_GID="$(stat -c '%g' /var/run/docker.sock)"
        if ! getent group "$DOCKER_GID" > /dev/null 2>&1; then
            groupadd -g "$DOCKER_GID" dockersock
        fi
        usermod -aG "$DOCKER_GID" app
    else
        echo "WARNING: /var/run/docker.sock not mounted - server instances cannot be managed" >&2
    fi

    exec gosu app "$@"
fi

# Container was started with a non-root --user override: run as-is.
exec "$@"
