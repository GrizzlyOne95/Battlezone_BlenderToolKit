from bz98tools.ogrefast.pure.binary import BinaryWriter


def test_chunk_length_is_backpatched():
    writer = BinaryWriter()
    with writer.chunk(0x3000):
        writer.u16(0x1234)
        writer.u32(0x56789ABC)

    data = writer.getvalue()
    assert data[:2] == b"\x00\x30"
    assert int.from_bytes(data[2:6], "little") == len(data)
    assert data[6:8] == b"\x34\x12"
    assert data[8:12] == b"\xbc\x9a\x78\x56"


def test_line_is_newline_terminated_utf8():
    writer = BinaryWriter()
    writer.line("[MeshSerializer_v1.100]")
    assert writer.getvalue() == b"[MeshSerializer_v1.100]\n"
