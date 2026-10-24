"""CLI: python -m app.generate --accounts 10000 --seed 42 --out data.json"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from .generator import generate
from .timeutil import parse_iso, utcnow_ms


def _reference(value: str | None) -> datetime:
    if not value or value == "now":
        return utcnow_ms()
    return parse_iso(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Генерация расширенного набора данных (формат SourceSnapshot)")
    parser.add_argument("--accounts", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--reference", default="now", help="опорное время ISO 8601 или now (по умолчанию)")
    parser.add_argument("--out", type=Path, default=Path("data.json"))
    parser.add_argument("--pretty", action="store_true", help="форматированный JSON с отступами")
    args = parser.parse_args(argv)
    if not 1 <= args.accounts <= 200_000:
        parser.error("--accounts должен быть от 1 до 200000")
    try:
        reference = _reference(args.reference)
    except ValueError as exc:
        parser.error(f"некорректное --reference: {exc}")

    started = time.perf_counter()
    data = generate(args.accounts, args.seed, reference)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        if args.pretty:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        else:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    print(
        f"{args.out}: {len(data['accounts'])} аккаунтов, {len(data['services'])} услуг, "
        f"{len(data['transactions'])} транзакций, reference_time={data['reference_time']}, "
        f"{time.perf_counter() - started:.1f} с",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
