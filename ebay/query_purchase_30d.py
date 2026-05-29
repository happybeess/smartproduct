from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

import ebay.purchase_history as ph


DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%b-%d-%Y",
    "%d-%b-%Y",
    "%Y/%m/%d",
)


def parse_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def iter_recent_records(records: Iterable[dict], cutoff: datetime) -> list[dict]:
    recent = []
    for rec in records:
        dt = parse_date(rec.get("purchase_date", ""))
        if dt and dt >= cutoff:
            recent.append(rec)
    return recent


def append_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    if not rows:
        return
    write_header = not path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def enable_fast_mode() -> None:
    original_sleep = ph.time.sleep

    def capped_sleep(seconds: float) -> None:
        original_sleep(min(seconds, 1.5))

    def capped_uniform(a: float, b: float) -> float:
        return min((a + b) / 2.0, 1.5)

    ph.time.sleep = capped_sleep
    ph.random.uniform = capped_uniform


def main() -> int:
    parser = argparse.ArgumentParser(description="Query eBay purchase history and export recent records")
    parser.add_argument("ids", nargs="+", help="eBay item ids")
    parser.add_argument("--cdp-url", default="http://localhost:9222")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--max-sales", type=int, default=100)
    parser.add_argument("--summary-csv", default="purchase_history_30d_summary.csv")
    parser.add_argument("--records-csv", default="purchase_history_30d_records.csv")
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args()

    if args.fast:
        enable_fast_mode()

    cutoff = datetime.now() - timedelta(days=args.days)
    results = ph.batch_fetch_purchase_history(
        args.ids,
        cdp_url=args.cdp_url,
        max_sales=args.max_sales,
    )

    summary_rows: list[dict] = []
    record_rows: list[dict] = []
    for result in results:
        item_id = result.get("item_id", "")
        title = result.get("item_title", "")
        total = result.get("total_purchases", 0)
        truncated_at = result.get("truncated_at", "")
        error = result.get("error", "")
        recent_records = iter_recent_records(result.get("records", []), cutoff)
        summary_rows.append(
            {
                "item_id": item_id,
                "title": title,
                "buy_it_now_price": result.get("buy_it_now_price", ""),
                "total_purchases": total,
                "recent_days": args.days,
                "recent_count": len(recent_records),
                "truncated_at": truncated_at,
                "error": error,
            }
        )
        for rec in recent_records:
            record_rows.append(
                {
                    "item_id": item_id,
                    "title": title,
                    "buy_it_now_price": result.get("buy_it_now_price", ""),
                    "username": rec.get("username", ""),
                    "price": rec.get("price", ""),
                    "quantity": rec.get("quantity", ""),
                    "purchase_date": rec.get("purchase_date", ""),
                }
            )

    append_csv(
        Path(args.summary_csv),
        summary_rows,
        ["item_id", "title", "buy_it_now_price", "total_purchases", "recent_days", "recent_count", "truncated_at", "error"],
    )
    append_csv(
        Path(args.records_csv),
        record_rows,
        ["item_id", "title", "buy_it_now_price", "username", "price", "quantity", "purchase_date"],
    )

    ok = sum(1 for row in summary_rows if not row["error"])
    hit = sum(1 for row in summary_rows if row["recent_count"] > 0 and not row["error"])
    print(f"batch_done ids={len(summary_rows)} ok={ok} recent_hit={hit}")
    for row in summary_rows:
        title = row["title"][:40]
        print(
            f"{row['item_id']}, recent{args.days}={row['recent_count']}, total={row['total_purchases']}, "
            f"truncated_at={row['truncated_at'] or 0}, error={row['error'] or '-'}, title={title}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
