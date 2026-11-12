"""A small BattlEye RCon client: enough to send a server a command and read
its answer.

Arma Reforger's RCON speaks the BattlEye RCon protocol over UDP (Server Config
wiki, `rcon`; https://www.battleye.com/downloads/BERConProtocol.txt). Every
packet is

    'B' 'E' | CRC32 of the rest, little-endian | 0xFF | type | payload

with three types: 0x00 login (payload: the password; the answer's payload is
0x01 for accepted, 0x00 for refused), 0x01 command (payload: a sequence byte and
the command text; the answer repeats the sequence byte and carries the reply,
possibly in several parts) and 0x02 a message the server sends by itself, which
has to be acknowledged with its sequence byte.

UDP loses packets, so a login or command that gets no answer is sent again a
few times before giving up.

Only the wire protocol lives here. Which commands exist is the game's business
(Server Management wiki); see rcon_admin for the ones the manager uses.
"""
import socket
import struct
import time
import zlib

LOGIN, COMMAND, MESSAGE = 0x00, 0x01, 0x02

DEFAULT_TIMEOUT = 2.0   # seconds to wait for one answer
DEFAULT_ATTEMPTS = 3    # times a login or command is sent before giving up
# Sent on the way out (Reforger 1.2.1+): frees the server's client slot at once
# instead of after its 45-second timeout. Not waited for.
LOGOUT_COMMAND = "@logout"


class RconError(Exception):
    """A user-facing RCON failure: no answer, wrong password, not listening."""


def packet(body: bytes) -> bytes:
    """Wrap `body` (type byte + payload) in the BattlEye header."""
    tail = b"\xff" + body
    return b"BE" + struct.pack("<I", zlib.crc32(tail) & 0xFFFFFFFF) + tail


def parse(data: bytes) -> bytes | None:
    """The body (type byte + payload) of a packet, or None if it is not one."""
    if len(data) < 8 or data[:2] != b"BE" or data[6] != 0xFF:
        return None
    if struct.unpack("<I", data[2:6])[0] != zlib.crc32(data[6:]) & 0xFFFFFFFF:
        return None
    return data[7:]


class Session:
    """One logged-in RCON connection. Use as a context manager."""

    def __init__(self, host: str, port: int, password: str,
                 timeout: float = DEFAULT_TIMEOUT, attempts: int = DEFAULT_ATTEMPTS):
        self.host, self.port, self.password = host, port, password
        self.timeout, self.attempts = timeout, attempts
        self._sock: socket.socket | None = None
        self._seq = 0

    # ---- lifecycle -----------------------------------------------------------

    def __enter__(self) -> "Session":
        try:
            info = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_DGRAM)[0]
            self._sock = socket.socket(info[0], socket.SOCK_DGRAM)
            self._sock.connect(info[4])
        except OSError as exc:
            self.close()
            raise RconError(f"Could not reach {self.host}:{self.port} ({exc})") from exc
        try:
            self._login()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._sock is not None:
                self._send(bytes([COMMAND, self._next_seq()]) + LOGOUT_COMMAND.encode())
        except OSError:
            pass
        self.close()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    # ---- wire ------------------------------------------------------------------

    def _send(self, body: bytes) -> None:
        self._sock.send(packet(body))

    def _next_seq(self) -> int:
        seq = self._seq
        self._seq = (self._seq + 1) % 256
        return seq

    def _receive(self, deadline: float) -> bytes | None:
        """The next valid packet body before `deadline`, or None when time is up.

        A message the server sends on its own is acknowledged here and skipped.
        """
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            self._sock.settimeout(left)
            try:
                data = self._sock.recv(65535)
            except TimeoutError:
                return None
            except ConnectionError as exc:
                # A connected UDP socket reports "port unreachable" this way.
                raise RconError(
                    f"Nothing is listening for RCON on {self.host}:{self.port}"
                ) from exc
            except OSError as exc:
                raise RconError(f"RCON connection failed ({exc})") from exc
            body = parse(data)
            if not body:
                continue
            if body[0] == MESSAGE and len(body) >= 2:
                self._send(bytes([MESSAGE, body[1]]))
                continue
            return body

    def _login(self) -> None:
        for _ in range(self.attempts):
            try:
                self._send(bytes([LOGIN]) + self.password.encode("utf-8"))
            except OSError as exc:
                raise RconError(f"Could not reach {self.host}:{self.port} ({exc})") from exc
            deadline = time.monotonic() + self.timeout
            while True:
                body = self._receive(deadline)
                if body is None:
                    break
                if body[0] == LOGIN and len(body) >= 2:
                    if body[1] == 0x01:
                        return
                    raise RconError("The server refused the RCON password")
        raise RconError(
            f"No answer from RCON on {self.host}:{self.port}. The server may still be "
            "starting, RCON may be off, or a firewall may be in the way."
        )

    def command(self, text: str) -> str:
        """Send one command; return the server's reply (often empty)."""
        if self._sock is None:
            raise RconError("The RCON session is closed")
        seq = self._next_seq()
        request = bytes([COMMAND, seq]) + text.encode("utf-8")
        parts: dict[int, bytes] = {}
        for _ in range(self.attempts):
            try:
                self._send(request)
            except OSError as exc:
                raise RconError(f"RCON connection failed ({exc})") from exc
            deadline = time.monotonic() + self.timeout
            while True:
                body = self._receive(deadline)
                if body is None:
                    break
                if body[0] != COMMAND or len(body) < 2 or body[1] != seq:
                    continue
                rest = body[2:]
                # A reply in several packets: 0x00, how many, which one, its text.
                if len(rest) >= 3 and rest[0] == 0x00:
                    total, index = rest[1], rest[2]
                    parts[index] = rest[3:]
                    if len(parts) >= total:
                        joined = b"".join(parts[i] for i in sorted(parts))
                        return joined.decode("utf-8", errors="replace")
                    continue
                return rest.decode("utf-8", errors="replace")
        raise RconError("The server did not answer the RCON command")


def run(host: str, port: int, password: str, commands: list[str],
        timeout: float = DEFAULT_TIMEOUT, attempts: int = DEFAULT_ATTEMPTS) -> list[str]:
    """Log in, send each command, return their replies in order."""
    with Session(host, port, password, timeout, attempts) as session:
        return [session.command(c) for c in commands]
