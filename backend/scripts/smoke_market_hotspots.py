"""Read-only live smoke for the homepage market hotspot DataHub contract."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.datahub.consumer import get_market_board_constituents, get_market_board_quotes


async def _run() -> int:
    try:
        industry = await get_market_board_quotes("industry")
        theme = await get_market_board_quotes("theme")
        industry_rows = list(industry.data or [])
        theme_rows = list(theme.data or [])
        if not industry_rows or not theme_rows:
            raise RuntimeError("行业或题材返回空数据")
        first = industry_rows[0]
        constituents = await get_market_board_constituents("industry", first.board_code, 1)
        rows = list(constituents.data or [])
        if not rows:
            raise RuntimeError("代表个股返回空数据")
        for label, result, count in (
            ("industry", industry, len(industry_rows)),
            ("theme", theme, len(theme_rows)),
            ("constituents", constituents, len(rows)),
        ):
            print(
                f"capability={result.capability.value} provider={result.provider} "
                f"rows={count} data_at={result.data_at.isoformat() if result.data_at else 'unknown'} "
                f"fields={','.join(sorted(result.data[0].model_dump().keys())) if result.data else '-'}"
            )
        return 0
    except Exception as exc:
        print(f"market hotspot smoke failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
