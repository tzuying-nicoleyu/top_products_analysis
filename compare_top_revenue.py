from google.cloud import bigquery
import pandas as pd
from datetime import datetime, timedelta

PROJECT_ID = "clean-pilot-456915-t0"
client = bigquery.Client(project=PROJECT_ID)

query = """
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
  AND t.type IN ('SalesOrd', 'RtnAuth')
  AND t.entity NOT IN (604610, 615265)
  AND c.category != 6
  AND tl.mainline = FALSE
  AND tl.taxline = FALSE
  AND tl.inventorylocation = 1
  AND tl.itemtype = 'InvtPart'
  AND i.manufacturer != 'D2'
ORDER BY t.trandate DESC
"""

df = client.query(query).to_dataframe()
so = df[df["type"] == "SalesOrd"]
so = so.groupby(by=["tranid", "item"], as_index=False).agg(
    quantity=("quantity", "sum"),
    netamount=("netamount", "sum"),
    costestimate=("costestimate", "sum"),
    status=("status", "first"),
    trandate=("trandate", "first"),
    type=("type", "first"),
)

six_months_ago = (datetime.today() - timedelta(days=182)).date()
so = so[so["trandate"] >= six_months_ago]
for col in ["quantity", "netamount", "costestimate"]:
    so[col] = so[col] * -1

so_status = pd.read_csv(r"c:\Users\Tzuying\Project\Downstream Analysis\so_status.csv")
so = so.merge(right=so_status, on="status", how="left")
valid_so = so[~so["status"].isin(["H", "C"])]

item_sales_agg = valid_so.groupby(by="item", as_index=False).agg(
    total_amount=("netamount", "sum"),
    total_quantity=("quantity", "sum"),
    total_costestimate=("costestimate", "sum"),
    so_count=("tranid", "nunique"),
    sales_days=("trandate", "nunique"),
)

item_sales_agg = (
    item_sales_agg.sort_values(by="total_amount", ascending=False)
    .assign(cum_pct=lambda x: x["total_amount"].cumsum() / x["total_amount"].sum())
    .reset_index(drop=True)
)
threshold = 0.8
x_val = item_sales_agg["cum_pct"].ge(threshold).idxmax()
top_revenue = item_sales_agg.iloc[0:x_val, :]

last = pd.read_csv(r"C:\Users\Tzuying\Project\Purchasing\output_csv_files\top_revenue_from_2025-01-01_to_2026-08-10.csv")

left_col = "item"
right_candidates = [c for c in ["item_id", "itemid", "id", "item"] if c in last.columns]
right_col = right_candidates[0] if right_candidates else None

print("top_revenue rows:", len(top_revenue))
print("top_revenue columns:", list(top_revenue.columns))
print("last rows:", len(last))
print("last columns:", list(last.columns))
print("compare columns:", left_col, right_col)

if right_col is None:
    raise ValueError("No matching item id column found in top_revenue_last")

a = set(top_revenue[left_col].astype(str).str.strip())
b = set(last[right_col].astype(str).str.strip())

overlap = a & b
only_top = a - b
only_last = b - a

print("overlap_count =", len(overlap))
print("top_revenue_only_count =", len(only_top))
print("top_revenue_last_only_count =", len(only_last))
print("overlap_values =", sorted(overlap)[:20])
print("top_revenue_only_values =", sorted(only_top)[:20])
print("top_revenue_last_only_values =", sorted(only_last)[:20])
