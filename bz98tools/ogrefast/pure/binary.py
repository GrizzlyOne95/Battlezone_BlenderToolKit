from __future__ import annotations

import io
import struct
from contextlib import contextmanager
from typing import Iterator


class BinaryWriter:
    """Little-endian OGRE serializer writer with back-patched chunk lengths."""

    def __init__(self) -> None:
        self._stream = io.BytesIO()

    @property
    def offset(self) -> int:
        return self._stream.tell()

    def getvalue(self) -> bytes:
        return self._stream.getvalue()

    def write(self, data: bytes | bytearray | memoryview) -> None:
        self._stream.write(data)

    def u8(self, value: int) -> None:
        self.write(struct.pack("<B", value))

    def bool(self, value: bool) -> None:
        self.u8(1 if value else 0)

    def u16(self, value: int) -> None:
        self.write(struct.pack("<H", value))

    def u32(self, value: int) -> None:
        self.write(struct.pack("<I", value))

    def f32(self, value: float) -> None:
        self.write(struct.pack("<f", value))

    def vec3(self, value: tuple[float, float, float]) -> None:
        self.write(struct.pack("<3f", *value))

    def line(self, value: str) -> None:
        self.write(value.encode("utf-8"))
        self.write(b"\n")

    @contextmanager
    def chunk(self, chunk_id: int) -> Iterator[None]:
        """Write an OGRE chunk header and back-patch its total byte length."""
        start = self.offset
        self.u16(chunk_id)
        self.u32(0)
        yield
        end = self.offset
        current = self.offset
        self._stream.seek(start + 2)
        self.u32(end - start)
        self._stream.seek(current)
