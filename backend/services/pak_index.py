"""Which files a mod's .pak holds, read from its file table (#270).

A script that fails to compile is logged by its path alone
(`scripts/Game/Player/RO_V_PlayerController.c`): the engine merges every mod
into one file system and does not say which mod the file came from. The mod
folders on disk do: each .pak lists its files.

The layout (PAC1) is taken from the open-source `enfusion_pak` parser, not from
Bohemia:

    "FORM" u32-BE size "PAC1"
    chunks, each: 4-char tag, u32-BE body length, body
      HEAD  28 bytes
      DATA  the files' bytes: skipped with a seek, never read
      FILE  the file table, entries in depth-first order:
              u8 kind (0 folder, 1 file), u8 name length, name
              folder: u32-LE number of children
              file:   24 bytes (offset, sizes, flags, timestamp)

So only the table is read, however large the pak. A table that does not parse
that way is still searched for the file's own name (`contains` then answers
from that alone), and a file that is no pak at all holds nothing.
"""
import struct
from pathlib import Path, PurePosixPath

TABLE_LIMIT = 64 * 1024 * 1024   # a file table larger than this is not read
TAIL_BYTES = 8 * 1024 * 1024     # what is searched when the chunks don't parse
ENTRY_LIMIT = 2_000_000
CHUNK_LIMIT = 64                 # chunks walked before giving up
_FILE_FIELDS = 24


class PakTable:
    """One pak's file table: its paths (lower case, `/`), or, when the table
    did not parse, only its raw bytes to search."""

    def __init__(self, paths: set[str] | None, raw: bytes):
        self.paths = paths
        self.raw = raw.lower() if paths is None else b""

    def contains(self, path: str) -> bool:
        path = path.replace("\\", "/").lower()
        if self.paths is not None:
            return path in self.paths
        name = PurePosixPath(path).name.encode("utf-8", errors="replace")
        # As an entry: the file kind, the name's length, the name.
        return 0 < len(name) < 256 and (bytes([1, len(name)]) + name).lower() in self.raw


def _table_bytes(f) -> bytes | None:
    """The FILE chunk's body, or None when the chunks are not as expected."""
    if f.read(4) != b"FORM":
        return None
    f.read(4)
    if f.read(4) != b"PAC1":
        return None
    for _ in range(CHUNK_LIMIT):
        head = f.read(8)
        if len(head) < 8:
            return None
        tag, length = head[:4], struct.unpack(">I", head[4:])[0]
        if tag == b"FILE":
            if length > TABLE_LIMIT:
                return None
            body = f.read(length)
            return body if len(body) == length else None
        f.seek(length, 1)
    return None


def _walk(table: bytes) -> set[str]:
    """Every file path in a file table. Raises ValueError on a table that is
    not laid out as described above."""
    out: set[str] = set()
    pos, entries = 0, 0
    # One frame per open folder: [its path, children still to read].
    stack: list[list] = []
    while pos < len(table):
        entries += 1
        if entries > ENTRY_LIMIT or pos + 2 > len(table):
            raise ValueError("file table")
        kind, size = table[pos], table[pos + 1]
        pos += 2
        name = table[pos:pos + size].decode("utf-8", errors="replace")
        pos += size
        parent = stack[-1][0] if stack else ""
        path = f"{parent}/{name}" if parent and name else parent or name
        if stack:
            stack[-1][1] -= 1
        if kind == 0:
            if pos + 4 > len(table):
                raise ValueError("file table")
            children = struct.unpack_from("<I", table, pos)[0]
            pos += 4
            stack.append([path, children])
        elif kind == 1:
            pos += _FILE_FIELDS
            if pos > len(table):
                raise ValueError("file table")
            out.add(path.lower())
        else:
            raise ValueError("file table")
        while stack and stack[-1][1] <= 0:
            stack.pop()
    return out


def read(pak: Path) -> PakTable | None:
    """A pak's file table, or None for a file that can't be read."""
    try:
        with pak.open("rb") as f:
            table = _table_bytes(f)
            if table is not None:
                try:
                    return PakTable(_walk(table), b"")
                except ValueError:
                    return PakTable(None, table)
            size = f.seek(0, 2)
            f.seek(max(0, size - TAIL_BYTES))
            return PakTable(None, f.read(TAIL_BYTES))
    except OSError:
        return None


def _safe(path: str) -> bool:
    parts = PurePosixPath(path.replace("\\", "/")).parts
    return bool(parts) and ".." not in parts and not path.startswith(("/", "\\")) and ":" not in parts[0]


def owners(folders: dict[str, list[Path]], files: list[str]) -> dict[str, list[str]]:
    """Script path -> the ids of the mods that ship it, in the order given.

    `folders`: mod id -> its folder(s). A mod ships a file when one of its paks
    lists it, or when it lies loose in the folder (an unpacked mod).
    """
    wanted = [f for f in dict.fromkeys(files) if _safe(f)]
    out: dict[str, list[str]] = {f: [] for f in wanted}
    if not wanted:
        return out
    for mid, dirs in folders.items():
        tables: list[PakTable] = []
        for folder in dirs:
            try:
                paks = sorted(p for p in folder.glob("*.pak") if p.is_file())
            except OSError:
                paks = []
            tables.extend(t for t in map(read, paks) if t is not None)
        for path in wanted:
            loose = any((folder / path).is_file() for folder in dirs)
            if loose or any(t.contains(path) for t in tables):
                out[path].append(mid)
    return out
