# Downstream Analysis

Demand planning and top-product identification pipeline for Smiles First Corp, powered by **BigQuery** (NetSuite data mirror) and Python.

---

## Overview

This project contains two independent pipelines:

| Pipeline | Entry point | Schedule |
|---|---|---|
| **Top Items by Revenue / Margin / Qty** | `invoice_top_items.py` | Every Monday via GitHub Actions |
| **Demand Segmentation & Reorder Points** | `reorder_point.ipynb` | Run manually as needed |

Both pipelines read from the `clean-pilot-456915-t0.NetSuite` BigQuery dataset and exclude internal entities (ID 604610, 615265) and manufacturer D2.

---

## Repository Structure

```
Downstream Analysis/
├── invoice_top_items.py              # Automated top-items export script
├── reorder_point.ipynb               # Demand segmentation & ROP notebook
├── so_status.csv                     # NetSuite sales order status lookup
├── requirements.txt                  # Python dependencies
├── output/                           # CSV exports (gitignored)
├── Intermittent Inventory Setting Presentation.pptx
│                                     # Methodology reference — demand
│                                     #   segmentation theory (Croston/SBA)
└── .github/workflows/
    └── invoice_top_items.yml         # GitHub Actions schedule
```

---

## Pipeline 1 — Top Items Export (`invoice_top_items.py`)

### What it does

1. Pulls **customer invoices** (`CustInvc`) from BigQuery for a configurable look-back window (default: 6 months).
2. Filters to inventory items at location 1, paid-in-full status, excluding free-goods lines.
3. Applies an **80% Pareto cutoff** across three dimensions — revenue, margin, and quantity — to identify top-performing SKUs.
4. Exports a timestamped CSV to `output/`.

### Running locally

```bash
python invoice_top_items.py                  # default: last 6 months
python invoice_top_items.py --months 3
python invoice_top_items.py --months 12
python invoice_top_items.py --days 90        # exact day count
```

Requires `GOOGLE_APPLICATION_CREDENTIALS` pointing to a GCP service account JSON with BigQuery read access to `clean-pilot-456915-t0`.

### GitHub Actions

The workflow in `.github/workflows/invoice_top_items.yml` runs automatically **every Monday at 8 AM UTC**. It reads the service account key from the `GCP_SERVICE_ACCOUNT_KEY` repository secret and uploads the resulting CSV as an Actions artifact (retained 30 days).

To trigger a manual run: **Actions → Invoice Top Items Export → Run workflow**.

---

## Pipeline 2 — Demand Segmentation & Reorder Points (`reorder_point.ipynb`)

### What it does

1. Pulls **sales orders** (`SalesOrd`) from BigQuery — all open and fulfilled statuses (excludes Closed `H` and Cancelled `C`).
2. Identifies **top items** using the same 80% Pareto logic as Pipeline 1 (applied to sales order revenue).
3. Aggregates demand to **weekly buckets** (Mon–Sat, Canadian holidays excluded) using a custom business-day calendar.
4. Classifies each item's demand pattern using **ADI / CV²** thresholds from the Croston/Syntetos framework:

   | Segment | ADI | CV² |
   |---|---|---|
   | **Smooth** | < 1.32 | < 0.49 |
   | **Erratic** | < 1.32 | ≥ 0.49 |
   | **Intermittent** | ≥ 1.32 | < 0.49 |
   | **Lumpy** | ≥ 1.32 | ≥ 0.49 |
   | **Sparse** | Sales weeks ≤ 1 or insufficient data | — |

   > **ADI** = total weeks ÷ non-zero weeks (measures how often demand occurs)  
   > **CV²** = (std / mean)² calculated on non-zero weeks only (measures variability of demand magnitude)

5. Forecasts demand rate using **Croston / SBA** (Syntetos-Boylan Approximation) per item.
6. Calculates **Safety Stock (SS)** and **Reorder Point (ROP)** by segment:
   - **Smooth** — Bootstrap simulation, 70% service level
   - **Erratic** — Bootstrap simulation, 80% service level
   - **Intermittent** — Negative Binomial distribution, 70% service level
   - **Lumpy** — Demand probability model, SS = 20% of lead-time demand
   - **Sparse** — SS = 0, ROP = 0 (order on demand)

For the theoretical background on demand segmentation and the Croston/SBA method, see **`Intermittent Inventory Setting Presentation.pptx`**.

### Running the notebook

1. Open `reorder_point.ipynb` in Jupyter.
2. Ensure `GOOGLE_APPLICATION_CREDENTIALS` is set (or a GCP key file is present).
3. Run all cells top to bottom. Memory-cleanup cells (`gc.collect()`) are included between heavy steps.
4. Intermediate outputs (`demand_profile_weekly.csv`, `top_demand_profile_weekly.csv`, `weekly_demand.csv`) are written to the project root for inspection.

**Lead time source:** The notebook merges lead time from a separate BigQuery table. Items with no lead time on record default to **14 days**.

---

## Setup

### Prerequisites

- Python 3.11+
- GCP service account with `bigquery.dataViewer` on `clean-pilot-456915-t0`

### Install dependencies

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Authenticate with BigQuery

```bash
# Option A — service account key file
set GOOGLE_APPLICATION_CREDENTIALS=path\to\key.json   # Windows
export GOOGLE_APPLICATION_CREDENTIALS=path/to/key.json # macOS/Linux

# Option B — Application Default Credentials (if you have gcloud installed)
gcloud auth application-default login
```

---

## Key configuration constants

| Constant | File | Default | Purpose |
|---|---|---|---|
| `PARETO_THRESHOLD` | `invoice_top_items.py` | `0.8` | 80% Pareto cutoff |
| `PROJECT_ID` | `invoice_top_items.py` | `clean-pilot-456915-t0` | GCP project |
| `review_cycle_weeks` | `reorder_point.ipynb` | `30 / 7 ≈ 4.3 wk` | Review cycle for ROP |
| `alpha` (Croston) | `reorder_point.ipynb` | `0.2` Smooth/Erratic, `0.1` others | Smoothing factor |

---

## Data sources (BigQuery — `clean-pilot-456915-t0.NetSuite`)

| Table | Used by | Purpose |
|---|---|---|
| `transaction` | Both | Order header (type, date, status, entity) |
| `transactionline` | Both | Line-level quantity, amount, cost, location |
| `customer` | Both | Entity category (used for exclusions) |
| `item` | Both | Item name, class, manufacturer |

---

## `so_status.csv`

Quick reference for NetSuite sales order status codes used in filter logic:

| Code | Meaning |
|---|---|
| H | Closed |
| C | Cancelled |
| A | Pending Approval |
| B | Pending Fulfillment |
| D | Partially Fulfilled |
| E | Pending Billing / Partially Fulfilled |
| F | Pending Billing |
| G | Billed |
