"""
invoice_top_items.py
--------------------
Pulls invoice data from BigQuery, identifies top items by revenue,
margin, and quantity, then exports results to a timestamped CSV.

Usage:
    python invoice_top_items.py                  # default: 6 months
    python invoice_top_items.py --months 3       # last 3 months
    python invoice_top_items.py --months 12      # last 12 months
    python invoice_top_items.py --days 90        # last 90 days (exact)

GitHub Actions: set MONTHS or DAYS as env variables, or pass as args.
"""

import argparse
import os
from datetime import datetime, timedelta, date

import pandas as pd
from google.cloud import bigquery


# ── Configuration ─────────────────────────────────────────────────────────────

PROJECT_ID = "clean-pilot-456915-t0"
PARETO_THRESHOLD = 0.8      # 80% cumulative cutoff for revenue & margin



# ── Argument parsing ───────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Invoice top-items export")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--months", type=int, default=6,
        help="Number of months to look back (default: 6)"
    )
    group.add_argument(
        "--days", type=int,
        help="Number of days to look back (overrides --months if set)"
    )
    return parser.parse_args()


def get_start_date(months: int, days: int | None) -> date:
    today = date.today() - timedelta(days=1)  # anchor to yesterday, matching BQ query
    if days is not None:
        return today - timedelta(days=days)
    # Approximate months as 30 days each — avoids dateutil dependency
    return today - timedelta(days=months * 30)


# ── BigQuery pull ──────────────────────────────────────────────────────────────

QUERY = """
SELECT
    t.id,
    t.tranid,
    t.trandate,
    t.status,
    t.type,
    tl.item,
    i.fullname,
    i.class,
    i.manufacturer,
    c.category,
    tl.quantity,
    tl.quantitybackordered,
    tl.netamount,
    tl.rate,
    tl.costestimate,
    tl.inventorylocation,
    tl.custcolfree_goods_checkbox
FROM `clean-pilot-456915-t0.NetSuite.transaction` t
JOIN `clean-pilot-456915-t0.NetSuite.transactionline` tl ON t.id = tl.transaction
JOIN `clean-pilot-456915-t0.NetSuite.customer` c ON t.entity = c.id
LEFT JOIN `clean-pilot-456915-t0.NetSuite.item` i ON tl.item = i.id
WHERE t.trandate <= DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
  AND t.type IN ('CustInvc')
  AND t.entity NOT IN (604610, 615265)
  AND c.category != 6
  AND tl.mainline = FALSE
  AND tl.taxline = FALSE
  AND tl.inventorylocation = 1
  AND tl.itemtype = 'InvtPart'
  AND i.manufacturer != 'D2'
ORDER BY t.trandate DESC
"""

def fetch_data(client: bigquery.Client) -> pd.DataFrame:
    print("Fetching invoice data from BigQuery...")
    df = client.query(QUERY).to_dataframe()
    print(f"  → {len(df):,} rows fetched")
    return df


# ── Transformation ─────────────────────────────────────────────────────────────

def transform(df: pd.DataFrame, start_date: date) -> pd.DataFrame:
    # Deduplicate: group by invoice + item
    inv = df.groupby(["tranid", "item"], as_index=False).agg(
        quantity      = ("quantity",      "sum"),
        netamount     = ("netamount",     "sum"),
        costestimate  = ("costestimate",  "sum"),
        status        = ("status",        "first"),
        trandate      = ("trandate",      "first"),
        fullname      = ("fullname",      "first"),
        class_        = ("class",         "first"),
        manufacturer  = ("manufacturer",  "first"),
        type          = ("type",          "first"),
    )

    # Filter to analysis window
    inv["trandate"] = pd.to_datetime(inv["trandate"]).dt.date
    inv = inv[inv["trandate"] >= start_date]
    print(f"  → {len(inv):,} rows after {start_date} filter")

    # Flip signs (invoices are negative in NetSuite)
    for col in ["quantity", "netamount", "costestimate"]:
        inv[col] = inv[col] * -1

    # Paid In Full only (status B)
    inv = inv[inv["status"] == "B"]
    print(f"  → {len(inv):,} rows after status=B filter")

    return inv


# ── Aggregation & top-item logic ───────────────────────────────────────────────

def aggregate(inv: pd.DataFrame) -> pd.DataFrame:
    inv["margin"] = inv["netamount"] - inv["costestimate"]
    agg = inv.groupby("item", as_index=False).agg(
        fullname         = ("fullname",     "first"),
        manufacturer     = ("manufacturer", "first"),
        class_           = ("class_",       "first"),
        total_amount     = ("netamount",    "sum"),
        total_quantity   = ("quantity",     "sum"),
        total_costestimate = ("costestimate", "sum"),
        total_margin     = ("margin",       "sum"),
    )
    return agg


def pareto_cutoff(agg: pd.DataFrame, sort_col: str, threshold: float) -> set:
    """Return the set of item IDs that make up `threshold` of `sort_col`."""
    sorted_df = agg.sort_values(by=sort_col, ascending=False).copy()
    sorted_df["cum_pct"] = sorted_df[sort_col].cumsum() / sorted_df[sort_col].sum()
    cutoff_idx = sorted_df["cum_pct"].ge(threshold).idxmax()
    return set(sorted_df.loc[:cutoff_idx, "item"])


def label_top_items(agg: pd.DataFrame) -> pd.DataFrame:
    top_revenue  = pareto_cutoff(agg, "total_amount",   PARETO_THRESHOLD)
    top_margin   = pareto_cutoff(agg, "total_margin",   PARETO_THRESHOLD)
    top_quantity = pareto_cutoff(agg, "total_quantity", PARETO_THRESHOLD)

    agg["top_revenue"]  = agg["item"].isin(top_revenue)
    agg["top_margin"]   = agg["item"].isin(top_margin)
    agg["top_quantity"] = agg["item"].isin(top_quantity)
    agg["top_all"]      = agg["top_revenue"] | agg["top_margin"] | agg["top_quantity"]

    print(f"  → Top revenue items : {len(top_revenue):,}")
    print(f"  → Top margin items  : {len(top_margin):,}")
    print(f"  → Top quantity items: {len(top_quantity):,}")
    print(f"  → In either three      : {agg['top_all'].sum():,}")

    return agg[agg["item"].isin(top_revenue| top_margin|top_quantity)]


# ── Export ─────────────────────────────────────────────────────────────────────

def export(agg: pd.DataFrame, start_date: date, output_dir: str = "output") -> str:
    os.makedirs(output_dir, exist_ok=True)
    yesterday_str = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    filename  = f"top_items_from_{start_date}_run_{yesterday_str}.csv"
    filepath  = os.path.join(output_dir, filename)
    agg.to_csv(filepath, index=False)
    print(f"  → Saved: {filepath}")
    return filepath


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    start_date = get_start_date(args.months, args.days)
    yesterday_str = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"\n=== Invoice Top Items ===")
    print(f"Analysis window : {start_date} → {yesterday_str}")
    print(f"Pareto threshold: {int(PARETO_THRESHOLD * 100)}%\n")

    client = bigquery.Client(project=PROJECT_ID)

    df  = fetch_data(client)
    inv = transform(df, start_date)
    agg = aggregate(inv)
    agg = label_top_items(agg)
    export(agg, start_date)

    print("\nDone.")


if __name__ == "__main__":
    main()
