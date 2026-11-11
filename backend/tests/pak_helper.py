"""Writes a .pak laid out as pak_index reads one (PAC1), for tests."""
import struct
from pathlib import Path


def _entries(tree: dict) -> bytes:
    out = b""
    for name, child in tree.items():
        raw = name.encode()
        if isinstance(child, dict):
            out += bytes([0, len(raw)]) + raw + struct.pack("<I", len(child)) + _entries(child)
        else:
            out += bytes([1, len(raw)]) + raw + b"\0" * 24
    return out


def write_pak(path: Path, files: list[str], data: bytes = b"x" * 100) -> Path:
    """A pak holding the given file paths (their contents are not written)."""
    tree: dict = {}
    for f in files:
        node = tree
        *folders, name = f.split("/")
        for part in folders:
            node = node.setdefault(part, {})
        node[name] = None
    table = bytes([0, 0]) + struct.pack("<I", len(tree)) + _entries(tree)
    body = (b"HEAD" + struct.pack(">I", 0x1C) + b"\0" * 0x1C
            + b"DATA" + struct.pack(">I", len(data)) + data
            + b"FILE" + struct.pack(">I", len(table)) + table)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"FORM" + struct.pack(">I", len(body) + 4) + b"PAC1" + body)
    return path
