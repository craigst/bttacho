"""The .ddd container format.

A .ddd is a flat concatenation of blocks:

    [file_id: 2 bytes BE][rec_type: 1][length: 2 bytes BE][data: length]

rec_type 0 is file content, rec_type 1 is its signature. Only rec_type 0 is
parsed back. The writer and reader below must agree -- they are the single
definition of this project's framing of the official download.
"""

from typing import Dict, Iterator, Tuple

REC_DATA = 0x00
REC_SIGNATURE = 0x01

HEADER_LEN = 5


def pack_block(fid: int, data: bytes, is_signature: bool = False) -> bytes:
    """Serialise one block."""
    return bytes([
        (fid >> 8) & 0xFF, fid & 0xFF,
        REC_SIGNATURE if is_signature else REC_DATA,
        (len(data) >> 8) & 0xFF, len(data) & 0xFF,
    ]) + bytes(data)


def iter_blocks(raw: bytes) -> Iterator[Tuple[int, int, bytes]]:
    """Yield (file_id, rec_type, data) for every well-formed block.

    Stops cleanly at the first truncated block rather than raising -- a partial
    download should surface whatever it did manage to read.
    """
    pos = 0
    while pos + HEADER_LEN <= len(raw):
        fid = (raw[pos] << 8) | raw[pos + 1]
        rec_type = raw[pos + 2]
        length = (raw[pos + 3] << 8) | raw[pos + 4]
        pos += HEADER_LEN

        if pos + length > len(raw):
            break

        yield fid, rec_type, raw[pos:pos + length]
        pos += length


def read_files(raw: bytes) -> Dict[int, bytes]:
    """Map file_id -> content for rec_type 0 blocks only."""
    return {fid: data for fid, rec, data in iter_blocks(raw) if rec == REC_DATA}
