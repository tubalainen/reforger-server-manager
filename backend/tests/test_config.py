from config import _port_range


def test_port_range_valid():
    assert _port_range("2001-2020", (1, 2)) == (2001, 2020)


def test_port_range_single_port():
    assert _port_range("5000-5000", (1, 2)) == (5000, 5000)


def test_port_range_malformed_falls_back():
    for raw in (None, "", "abc", "10", "20-10", "-5-10", "0-10"):
        assert _port_range(raw, (2001, 2020)) == (2001, 2020)
