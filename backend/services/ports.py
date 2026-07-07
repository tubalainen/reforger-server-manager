"""Lease unique host UDP ports to instances from the configured ranges."""


class PortExhaustedError(Exception):
    """No free port left in a range."""


def first_free(lo: int, hi: int, taken: set[int]) -> int:
    """Lowest port in [lo, hi] not in `taken`. Raises PortExhaustedError."""
    for port in range(lo, hi + 1):
        if port not in taken:
            return port
    raise PortExhaustedError(f"no free port in range {lo}-{hi}")


def lease(
    game_range: tuple[int, int],
    a2s_range: tuple[int, int],
    rcon_range: tuple[int, int],
    used_game: set[int],
    used_a2s: set[int],
    used_rcon: set[int],
) -> tuple[int, int, int]:
    """Return (game, a2s, rcon) ports free across all existing instances."""
    return (
        first_free(*game_range, used_game),
        first_free(*a2s_range, used_a2s),
        first_free(*rcon_range, used_rcon),
    )
