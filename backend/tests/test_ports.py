import pytest

from services import ports


def test_first_free_picks_lowest():
    assert ports.first_free(2001, 2020, set()) == 2001
    assert ports.first_free(2001, 2020, {2001, 2002}) == 2003


def test_first_free_skips_gaps():
    assert ports.first_free(2001, 2020, {2001, 2003}) == 2002


def test_first_free_exhausted():
    with pytest.raises(ports.PortExhaustedError):
        ports.first_free(2001, 2002, {2001, 2002})


def test_lease_returns_distinct_from_each_range():
    game, a2s, rcon = ports.lease(
        (2001, 2020), (17777, 17796), (19999, 20018),
        {2001}, set(), {19999},
    )
    assert game == 2002
    assert a2s == 17777
    assert rcon == 20000
