import json
import time
from datetime import datetime, timedelta, timezone

import akshare as ak
import efinance as ef
import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ------------------------------------------------------------------
# 1. Fund Data
# ------------------------------------------------------------------

# fmt: off
fund_codes = [
    "019548", "019547", "016702", "016701", "019442", "019441",
    "016453", "016452", "019305", "017641", "019173", "019172",
    "018147", "539002", "539001", "000043", "022664", "014978",
    "040046", "006479", "270042", "096001", "008401", "008971",
    "000834", "019736", "015202", "020712", "020713",
    "001668", "012752", "018967", "018966", "017437", "017436",
    "501312", "017204"
]

investment_amounts = [
    10, 10, 10, 100, 10, 10,
    10, 10, 10, 10, 10, 10,
    10, 100, 10, 10, 10, 10,
    10, 5, 5, 10, 10, 10,
    10, 10, 0, 10, 10,
    0, 0, 0, 0, 0, 0,
    0, 0
]
# fmt: on

if len(investment_amounts) != len(fund_codes):
    print(
        f"Warning: Amount count ({len(investment_amounts)}) does not match fund count ({len(fund_codes)}). Truncating/Padding."
    )
    if len(investment_amounts) < len(fund_codes):
        investment_amounts += [10] * (len(fund_codes) - len(investment_amounts))
    else:
        investment_amounts = investment_amounts[: len(fund_codes)]


def call_with_retry(func, *args, retries=3, delay=1, **kwargs):
    last_exception = None
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if attempt == retries - 1:
                break
            print(f"Retry {attempt+1}/{retries} for {func.__name__} after error: {e}")
            time.sleep(delay * (2**attempt))
    raise last_exception


def get_fund_limit(fund_code: str):
    try:
        df = call_with_retry(ak.fund_purchase_em)
        row = df[df["基金代码"] == fund_code]
        if not row.empty:
            limit = row.iloc[0]["日累计限定金额"]
            purchase_status = row.iloc[0]["申购状态"]
            fee = row.iloc[0]["手续费"]
            return {
                "基金代码": fund_code,
                "基金简称": row.iloc[0]["基金简称"],
                "日累计限额": limit,
                "申购状态": purchase_status,
                "手续费": fee,
            }
        else:
            return {"error": f"Fund code {fund_code} not found"}
    except Exception as e:
        return {"error": f"Query failed after retries: {str(e)}"}


def get_fund_data(code):
    try:
        df = call_with_retry(ef.fund.get_quote_history, code)
        df["Date"] = pd.to_datetime(df["日期"])
        df["Nav"] = pd.to_numeric(df["单位净值"], errors="coerce")
        df = df.dropna(subset=["Nav"])
        df = df.sort_values("Date").reset_index(drop=True)

        try:
            base_info = call_with_retry(ef.fund.get_base_info, code)
            fund_name = base_info.get("基金简称", code)
        except Exception:
            fund_name = code
        return df[["Date", "Nav"]], fund_name
    except Exception as e:
        print(f"Failed to fetch {code} after retries: {e}")
        return None, None


def calc_period_metrics(df, end_date, start_date):
    end_ts = pd.Timestamp(end_date)
    start_ts = pd.Timestamp(start_date)
    mask = (df["Date"] >= start_ts) & (df["Date"] <= end_ts)
    period_df = df.loc[mask].copy()
    if len(period_df) < 2:
        return np.nan, np.nan, np.nan

    start_nav = period_df["Nav"].iloc[0]
    end_nav = period_df["Nav"].iloc[-1]
    total_return = (end_nav - start_nav) / start_nav

    days = (period_df["Date"].iloc[-1] - period_df["Date"].iloc[0]).days
    if days > 0 and total_return > -1:
        annual_ret = (1 + total_return) ** (365 / days) - 1
    else:
        annual_ret = np.nan

    cum_max = period_df["Nav"].cummax()
    drawdown = (period_df["Nav"] - cum_max) / cum_max
    max_drawdown = drawdown.min()
    return total_return, annual_ret, max_drawdown


def calmar_ratio(annual_ret, max_drawdown):
    if pd.isna(max_drawdown) or max_drawdown == 0:
        return np.nan
    return annual_ret / abs(max_drawdown)


def compute_total_score(row, periods):
    weight_map = {"1M": 0.05, "3M": 0.10, "6M": 0.15, "1Y": 0.20, "2Y": 0.25, "3Y": 0.25}
    ann_returns = []
    weights = []
    for label in periods.keys():
        ann_ret = row.get(f"{label}_AnnReturn")
        calmar = row.get(f"{label}_Calmar")
        w = weight_map.get(label, 0)
        if not pd.isna(ann_ret) and not pd.isna(calmar) and w > 0:
            ann_returns.append(ann_ret)
            weights.append(w)
    if not weights:
        return np.nan
    weights = np.array(weights) / np.sum(weights)
    return np.average(ann_returns, weights=weights)


def generate_funds_json():
    today = datetime.now().date()
    periods = {
        "1M": today - timedelta(days=30),
        "3M": today - timedelta(days=90),
        "6M": today - timedelta(days=180),
        "1Y": today - timedelta(days=365),
        "2Y": today - timedelta(days=730),
        "3Y": today - timedelta(days=1095),
    }
    all_results = []
    for idx, code in enumerate(fund_codes):
        print(f"Processing {code}...")
        df, fund_name = get_fund_data(code)
        if df is None or df.empty:
            print(f"Skipping {code}, no data.")
            continue

        invest = investment_amounts[idx] if idx < len(investment_amounts) else np.nan

        limit_info = get_fund_limit(code)
        if "error" not in limit_info:
            limit = limit_info.get("日累计限额")
            purchase_status = limit_info.get("申购状态")
            fee = limit_info.get("手续费")
        else:
            limit = None
            purchase_status = None
            fee = None
            print(f"Limit query for {code}: {limit_info['error']}")

        row = {
            "Code": str(code),
            "Name": fund_name,
            "Money": invest,
            "Limit": limit,
            "PurchaseStatus": purchase_status,
            "Fee": fee,
        }

        for label, start_date in periods.items():
            if df["Date"].min().date() > start_date:
                total_ret, ann_ret, mdd = np.nan, np.nan, np.nan
            else:
                total_ret, ann_ret, mdd = calc_period_metrics(df, today, start_date)
            calmar = calmar_ratio(ann_ret, mdd)
            row[f"{label}_Return"] = total_ret
            row[f"{label}_AnnReturn"] = ann_ret
            row[f"{label}_MaxDrawdown"] = mdd
            row[f"{label}_Calmar"] = calmar

        all_results.append(row)

    result_df = pd.DataFrame(all_results)
    result_df["Code"] = result_df["Code"].astype(str)
    result_df["TotalScore"] = result_df.apply(lambda r: compute_total_score(r, periods), axis=1)
    sorted_df = result_df.sort_values("TotalScore", ascending=False).reset_index(drop=True)
    sorted_df = sorted_df.replace({np.nan: None})
    records = sorted_df.to_dict(orient="records")
    with open("funds.json", "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print("\n✅ funds.json saved")


# ------------------------------------------------------------------
# 2. QQQ & PE Data
# ------------------------------------------------------------------

TICKER = "QQQ"
START_DATE = "2000-01-01"
END_DATE = datetime.today().strftime("%Y-%m-%d")
MAX_RETRIES = 2
RETRY_DELAYS = [30, 60]
INITIAL_CAPITAL = 1000.0
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def fetch_qqq():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    for attempt in range(MAX_RETRIES):
        try:
            print(f"Downloading {TICKER} (attempt {attempt+1}/{MAX_RETRIES})...")
            df = yf.download(
                TICKER,
                start=START_DATE,
                end=END_DATE,
                progress=False,
                session=session,
                auto_adjust=False,
            )
            if df.empty:
                raise ValueError("Empty DataFrame")
            close = df["Close"] if "Close" in df else df.xs("Close", axis=1, level=0)
            close = pd.to_numeric(close.squeeze(), errors="coerce").dropna()
            print(f"✅ Downloaded {len(close)} rows for {TICKER}")
            return close.to_frame("Close")
        except Exception as e:
            print(f"Error: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAYS[attempt])
            else:
                raise RuntimeError(f"Failed after {MAX_RETRIES} attempts") from e


def fetch_pe():
    url = "https://danjuanfunds.com/djapi/index_eva/pe_history/NDX?day=all"
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        pe_list = data.get("data", {}).get("index_eva_pe_growths", [])
        if not pe_list:
            raise ValueError("No PE data in response")
        rows = []
        for item in pe_list:
            dt = datetime.fromtimestamp(item["ts"] / 1000, tz=timezone.utc)
            rows.append({"Date": dt.strftime("%Y-%m-%d"), "PE": item["pe"]})
        df = pd.DataFrame(rows)
        df["Date"] = pd.to_datetime(df["Date"])
        df.sort_values("Date", inplace=True)
        print(f"✅ Fetched {len(df)} PE records")
        return df
    except Exception as e:
        print(f"❌ PE fetch failed: {e}")
        raise FileNotFoundError("No PE data available (API failed)") from e


def generate_qqq_pe_json():
    qqq = fetch_qqq()
    pe = fetch_pe()

    qqq.sort_index(inplace=True)
    pe.sort_values("Date", inplace=True)

    merged = pd.merge_asof(qqq, pe, left_index=True, right_on="Date", direction="backward")
    merged.set_index("Date", inplace=True)
    merged["PE"] = merged["PE"].ffill()
    merged.dropna(subset=["PE"], inplace=True)

    if merged.empty:
        raise ValueError("No overlapping data")

    start = merged.index.min().strftime("%Y-%m-%d")
    end = merged.index.max().strftime("%Y-%m-%d")
    print(f"Period: {start} → {end} ({len(merged)} days)")

    records = merged.reset_index()[["Date", "Close", "PE"]].copy()
    records["Date"] = records["Date"].dt.strftime("%Y-%m-%d")

    output = {
        "data": records.to_dict(orient="records"),
        "default_parameters": {
            "initial_capital": INITIAL_CAPITAL,
            "start_date": start,
            "end_date": end,
        },
    }

    with open("qqq_pe_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print("✅ qqq_pe_data.json saved")


# ------------------------------------------------------------------
# Main: run both data generation tasks
# ------------------------------------------------------------------
if __name__ == "__main__":
    generate_funds_json()
    generate_qqq_pe_json()
    print("\n🎉 All JSON files generated successfully.")