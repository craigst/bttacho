#!/usr/bin/env python3
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def parse_ddd(path: Path):
    data = path.read_bytes()
    size = len(data)
    idx = 0
    blocks = []
    errors = []

    while idx + 5 <= size:
        ef = data[idx : idx + 2]
        dtype = data[idx + 2]
        length = (data[idx + 3] << 8) | data[idx + 4]
        idx += 5

        if idx + length > size:
            errors.append(
                {
                    "offset": idx - 5,
                    "length": length,
                    "remaining": size - idx,
                    "error": "truncated_block",
                }
            )
            break

        payload = data[idx : idx + length]
        blocks.append(
            {
                "ef": ef.hex().upper(),
                "type": dtype,
                "length": length,
                "offset": idx - 5,
                "payload": payload,
            }
        )
        idx += length

    trailing = size - idx
    return data, blocks, errors, trailing


def summarize(blocks):
    counts = Counter((b["ef"], b["type"]) for b in blocks)
    by_ef = defaultdict(list)
    for b in blocks:
        by_ef[b["ef"]].append(b)
    return counts, by_ef


def main():
    parser = argparse.ArgumentParser(description="Inspect a tachograph DDD file.")
    parser.add_argument("path", nargs="?", default="driver.ddd", help="Path to .ddd file")
    parser.add_argument("--json", action="store_true", help="Output JSON summary")
    parser.add_argument(
        "--show-blocks", action="store_true", help="List all blocks with sizes"
    )
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"File not found: {path}")

    data, blocks, errors, trailing = parse_ddd(path)
    counts, by_ef = summarize(blocks)
    signed = any(b["type"] == 1 for b in blocks)

    if args.json:
        payload = {
            "path": str(path),
            "size_bytes": len(data),
            "blocks": [
                {
                    "ef": b["ef"],
                    "type": b["type"],
                    "length": b["length"],
                    "offset": b["offset"],
                }
                for b in blocks
            ],
            "signed": signed,
            "errors": errors,
            "trailing_bytes": trailing,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    print(f"file: {path}")
    print(f"size_bytes: {len(data)}")
    print(f"blocks: {len(blocks)}")
    print(f"signed: {signed}")
    if errors:
        print(f"errors: {len(errors)}")
        for err in errors:
            print(
                f"  {err['error']} at offset={err['offset']} length={err['length']} remaining={err['remaining']}"
            )
    if trailing:
        print(f"trailing_bytes: {trailing}")

    print("\nblock_counts:")
    for (ef, dtype), count in sorted(counts.items()):
        dtype_label = "data" if dtype == 0 else "sig" if dtype == 1 else f"type{dtype}"
        print(f"  {ef} {dtype_label} count={count}")

    if args.show_blocks:
        print("\nblocks_detail:")
        for b in blocks:
            dtype_label = (
                "data" if b["type"] == 0 else "sig" if b["type"] == 1 else f"type{b['type']}"
            )
            print(f"  {b['ef']} {dtype_label} len={b['length']}")


if __name__ == "__main__":
    main()
