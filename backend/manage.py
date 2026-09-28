"""Commands for whoever manages the machine, run inside the manager container.

    docker exec -u app <stack>-manager python manage.py reset-password

`rsm reset-password` runs exactly that. Team admins never need these: they
exist for the host administrator, who has a shell where team admins have only
the GUI (#204).
"""
import os
import sys

import auth
import models


def reset_password() -> int:
    """Forget the password set in the GUI; ADMIN_PASSWORD from .env applies again."""
    models.init_db()
    if not auth.clear_gui_password():
        print("No password was set in the GUI: the one in .env (ADMIN_PASSWORD) is in effect.")
        return 0
    # Whoever had the forgotten password is logged out too.
    auth.rotate_session_salt()
    print("The password set in the GUI was removed, and every session was logged out.")
    print("Sign in with ADMIN_PASSWORD from this install's .env, then set a new one under")
    print("System › Account.")
    return 0


COMMANDS = {"reset-password": reset_password}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in COMMANDS:
        print(f"usage: python manage.py {{{'|'.join(COMMANDS)}}}", file=sys.stderr)
        return 2
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        # Files written as root (the database journal, the session salt) would be
        # unreadable to the manager, which runs as 'app'.
        print("Run this as the app user: docker exec -u app <stack>-manager "
              "python manage.py " + argv[0], file=sys.stderr)
        return 2
    return COMMANDS[argv[0]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
