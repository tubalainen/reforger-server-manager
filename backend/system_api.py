"""Host-facing helpers the GUI needs: the firewall rules a player-facing
server requires, rendered from the configured port ranges (issue #51).

Players reach an instance over UDP on its game port (join) and its A2S port
(server browser). RCON and the web GUI are deliberately absent: neither should
ever face the internet.
"""
from fastapi import APIRouter, Depends

import auth
import config
from services import docker_service

router = APIRouter(prefix="/api/system", tags=["system"])

FIREWALL_RULE_NAME = "Arma Reforger (game + A2S)"


def _fmt(rng: tuple[int, int]) -> str:
    return f"{rng[0]}-{rng[1]}"


def windows_firewall_command(game: tuple[int, int], a2s: tuple[int, int]) -> str:
    """Single elevated-PowerShell rule covering both player-facing ranges."""
    return (
        f'New-NetFirewallRule -DisplayName "{FIREWALL_RULE_NAME}" '
        f"-Direction Inbound -Action Allow -Protocol UDP "
        f"-LocalPort {_fmt(game)},{_fmt(a2s)}"
    )


def linux_firewall_command(game: tuple[int, int], a2s: tuple[int, int]) -> str:
    """ufw uses LO:HI ranges and one rule per range."""
    return (
        f"sudo ufw allow {game[0]}:{game[1]}/udp\n"
        f"sudo ufw allow {a2s[0]}:{a2s[1]}/udp"
    )


@router.get("/network")
async def network(_user: str = Depends(auth.require_session)):
    s = config.settings
    return {
        "game_port_range": _fmt(s.game_port_range),
        "a2s_port_range": _fmt(s.a2s_port_range),
        "rcon_port_range": _fmt(s.rcon_port_range),
        # Which command to show first; the GUI offers both regardless.
        "host": "windows" if docker_service.is_docker_desktop() else "linux",
        "firewall": {
            "windows": windows_firewall_command(s.game_port_range, s.a2s_port_range),
            "linux": linux_firewall_command(s.game_port_range, s.a2s_port_range),
        },
    }
