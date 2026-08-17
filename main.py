import json
import time
from datetime import datetime, timedelta, timezone

import akshare as ak
import efinance as ef
import numpy as np
import pandas as pd
import requests
import yfinance as yf
import os

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

code_to_amount = dict(zip(fund_codes, investment_amounts))

# ------------------------------------------------------------------
# 2. Common helpers & constants
# ------------------------------------------------------------------

CACHE_FILE = "us_funds_cache.json"
SCORE_JSON = "funds.json"
QQQ_JSON = "qqq_pe_data.json"

RISK_FREE_RATE = 0.03
_LIMIT_DF = None

# ---- US stock / keyword lists ----
# fmt: off
US_STOCK_NAMES = [
    "英伟达", "苹果", "微软", "谷歌", "亚马逊", "特斯拉", "Meta", "博通",
    "台积电", "阿斯麦", "奈飞", "AMD", "高通", "英特尔", "超微半导体",
    "Google", "Microsoft", "Amazon", "Tesla", "NVIDIA", "美光", "闪迪"
]
US_KEYWORDS = [
    "纳斯达克", "标普", "道琼斯", "费城", "美国", "美股", "境外", "QDII",
    "海外", "国际", "全球", "纳指", "SP500", "S&P", "Dow", "NASDAQ", "QQQ"
]
EXCLUDE_KEYWORDS = [
    "恒生", "债", "港股", "香港", "黄金", "医药", "生物", "消费",
    "石油", "油气", "A股", "海外中国", "房地产", "不动产", "医疗"
]
# fmt: on

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _to_str_safe(val):
    return "" if (pd.isna(val) or val is None) else str(val)


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


# ------------------------------------------------------------------
# 3. Fund limit data (shared)
# ------------------------------------------------------------------


def load_limit_data():
    global _LIMIT_DF
    if _LIMIT_DF is None:
        print("Fetching fund limit data from akshare...")
        _LIMIT_DF = ak.fund_purchase_em()
        if (
            "日累计限额" not in _LIMIT_DF.columns
            and "日累计限定金额" in _LIMIT_DF.columns
        ):
            _LIMIT_DF.rename(columns={"日累计限定金额": "日累计限额"}, inplace=True)
        print(f"Loaded {len(_LIMIT_DF)} limit records.")
    return _LIMIT_DF


def get_fund_limit(code):
    df = load_limit_data()
    row = df[df["基金代码"] == code]
    if not row.empty:
        return {
            "code": code,
            "name": row.iloc[0]["基金简称"],
            "daily_limit": row.iloc[0].get("日累计限额", None),
            "subscription_status": row.iloc[0].get("申购状态", None),
            "fee": _to_str_safe(row.iloc[0].get("手续费", None)),
        }
    return {
        "code": code,
        "daily_limit": None,
        "subscription_status": None,
        "fee": None,
        "error": f"Fund {code} not found",
    }


# ------------------------------------------------------------------
# 4. Period metrics & scoring helpers
# ------------------------------------------------------------------

def max_drawdown(navs):
    if not navs:
        return 0.0
    peak = navs[0]
    max_dd = 0.0
    for v in navs:
        if v > peak:
            peak = v
        if peak != 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def period_stats(nav_df, end_date, days):
    start = end_date - timedelta(days=days)
    segment = nav_df[(nav_df["日期"] >= start) & (nav_df["日期"] <= end_date)]
    if len(segment) < 2:
        return None, None, None, None, None

    # Check if the fund actually covers the entire period.
    if nav_df["日期"].min() > start:
        return None, None, None, None, None

    navs = segment["单位净值"].values
    returns = navs[1:] / navs[:-1] - 1
    cum_ret = navs[-1] / navs[0] - 1.0
    mdd = max_drawdown(navs.tolist())
    years = days / 365.0
    ann_ret = (1 + cum_ret) ** (1 / years) - 1 if years > 0 else None
    if len(returns) > 1:
        vol_annual = np.std(returns, ddof=1) * np.sqrt(252)
    else:
        vol_annual = np.nan
    calmar = cum_ret / mdd if mdd != 0 else (float("inf") if cum_ret > 0 else 0.0)
    return cum_ret, ann_ret, vol_annual, mdd, calmar


def compute_total_score(row, period_days, weight_map):
    ann_returns = []
    weights = []
    for label in period_days.keys():
        ann_ret = row.get(f"{label}_AnnReturn")
        calmar = row.get(f"{label}_Calmar")
        w = weight_map.get(label, 0)
        if not pd.isna(ann_ret) and not pd.isna(calmar) and w > 0:
            ann_returns.append(ann_ret)
            weights.append(w)
    if not weights:
        return np.nan
    weights = np.array(weights) / np.sum(weights)
    return float(np.average(ann_returns, weights=weights))


# ------------------------------------------------------------------
# 5. US fund identification & cache
# ------------------------------------------------------------------


def _is_us_fund(code, name=""):
    name = str(name) if not pd.isna(name) else ""
    for kw in EXCLUDE_KEYWORDS:
        if kw in name:
            return False
    if any(kw in name for kw in US_KEYWORDS):
        return True
    holdings = ef.fund.get_invest_position(code)
    if holdings is not None and not holdings.empty:
        top3 = holdings.head(3)
        if top3["股票简称"].str.strip().isin(US_STOCK_NAMES).any():
            return True
    return False


def load_us_fund_cache(refresh=False):
    if not refresh and os.path.exists(CACHE_FILE):
        print(f"Loading cache from {CACHE_FILE} …")
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return pd.DataFrame(json.load(f))

    print("Fetching full fund list …")
    df_all = ef.fund.get_fund_codes()
    verified = []
    total = len(df_all)
    start = time.time()

    for i, (_, row) in enumerate(df_all.iterrows()):
        code = row["基金代码"]
        if _is_us_fund(code, row["基金简称"]):
            verified.append({"基金代码": code, "基金简称": row["基金简称"]})
        if (i + 1) % 100 == 0:
            print(
                f"  Scanned {i+1}/{total} – found {len(verified)} US funds ({time.time()-start:.0f}s)"
            )

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(verified, f, ensure_ascii=False, indent=2)
    print(f"Done. Total US funds: {len(verified)}")
    return pd.DataFrame(verified)


# ------------------------------------------------------------------
# 6. QQQ data for comparison
# ------------------------------------------------------------------


def load_qqq_data(json_path=QQQ_JSON):
    if not os.path.exists(json_path):
        return None
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    df = pd.DataFrame(data["data"])
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    df["QQQ_Return"] = df["Close"].pct_change()
    return df[["Date", "QQQ_Return"]].dropna()


def compute_period_metrics(fund_ret_df, qqq_df, start_date, end_date, label):
    fund_period = fund_ret_df[
        (fund_ret_df["日期"] >= start_date) & (fund_ret_df["日期"] <= end_date)
    ]
    qqq_period = qqq_df[(qqq_df["Date"] >= start_date) & (qqq_df["Date"] <= end_date)]

    if fund_period.empty:
        return None
    if fund_ret_df["日期"].min() > start_date:
        return None

    merged = pd.merge(
        fund_period, qqq_period, left_on="日期", right_on="Date", how="inner"
    )
    if len(merged) < 10:
        return None

    fund_ret = merged["return"].values
    qqq_ret = merged["QQQ_Return"].values

    corr = np.corrcoef(fund_ret, qqq_ret)[0, 1] if len(fund_ret) > 1 else 0.0
    abs_corr = abs(corr) if not np.isnan(corr) else 0.0
    win_rate = np.mean(fund_ret > qqq_ret)
    both_up = (fund_ret > 0) & (qqq_ret > 0)
    both_down = (fund_ret < 0) & (qqq_ret < 0)
    same_dir_rate = (np.sum(both_up) + np.sum(both_down)) / len(fund_ret)

    if len(fund_ret) > 1 and np.std(qqq_ret) > 1e-9:
        slope, _ = np.polyfit(qqq_ret, fund_ret, 1)
        beta = slope
    else:
        beta = np.nan

    return {
        f"{label}_Correlation": corr,
        f"{label}_AbsCorrelation": abs_corr,
        f"{label}_WinRate": win_rate,
        f"{label}_SameDirectionRate": same_dir_rate,
        f"{label}_Beta": beta,
        f"{label}_CommonDays": len(merged),
    }


# ------------------------------------------------------------------
# 7. Main JSON generation (renamed to match base)
# ------------------------------------------------------------------

def generate_funds_json(refresh_cache=False):
    end_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"Using today's date: {end_date_str}")

    load_limit_data()

    funds = load_us_fund_cache(refresh=refresh_cache)
    if funds.empty:
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "funds": []}

    qqq_df = load_qqq_data()
    if qqq_df is None:
        print(
            "Warning: qqq_pe_data.json not found. Nasdaq-100 comparison will be skipped."
        )
    else:
        qqq_df = qqq_df.sort_values("Date")
        print(
            f"QQQ data loaded: {len(qqq_df)} records, range {qqq_df['Date'].min()} ~ {qqq_df['Date'].max()}"
        )

    period_days = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "2Y": 730, "3Y": 1095}
    weight_map = {
        "1M": 0.05,
        "3M": 0.10,
        "6M": 0.15,
        "1Y": 0.20,
        "2Y": 0.25,
        "3Y": 0.25,
    }

    end_date = pd.to_datetime(end_date_str)
    results = []
    total = len(funds)

    for idx, (_, row) in enumerate(funds.iterrows()):
        code = row["基金代码"]
        name = row["基金简称"]

        try:
            # Use call_with_retry for robustness (like base)
            nav_df = call_with_retry(ef.fund.get_quote_history, code)
            if nav_df is None or nav_df.empty:
                print(f"[{idx+1}/{total}] {code} {name} -> No NAV data, skipping")
                continue
            nav_df["日期"] = pd.to_datetime(nav_df["日期"])
            nav_df = nav_df.sort_values("日期")
            nav_df = nav_df[nav_df["日期"] <= end_date]
            if nav_df.empty:
                print(
                    f"[{idx+1}/{total}] {code} {name} -> No data before cutoff, skipping"
                )
                continue
            nav_df["单位净值"] = pd.to_numeric(nav_df["单位净值"], errors='coerce')

            # ---- Period statistics ----
            period_data = {}
            for label, days in period_days.items():
                cum, ann, vol, mdd, cal = period_stats(nav_df, end_date, days)
                sharpe = (
                    (ann - RISK_FREE_RATE) / vol
                    if (ann is not None and vol is not None and vol > 0)
                    else None
                )
                period_data[label] = {
                    "cumulative_return": cum,
                    "annualised_return": ann,
                    "volatility": vol,
                    "max_drawdown": mdd,
                    "calmar": cal,
                    "sharpe": sharpe,
                }

            # ---- Compute total score ----
            row_dict = {}
            for label in period_days:
                p = period_data.get(label, {})
                row_dict[f"{label}_AnnReturn"] = p.get("annualised_return")
                row_dict[f"{label}_Calmar"] = p.get("calmar")
            total_score = compute_total_score(row_dict, period_days, weight_map)
            if np.isnan(total_score):
                print(
                    f"[{idx+1}/{total}] {code} {name} -> Insufficient period data, skipping score"
                )
                continue

            # ---- Limit info ----
            limit = get_fund_limit(code)
            status = limit.get("subscription_status")
            daily_limit = limit.get("daily_limit")

            # ---- Money from investment list ----
            money = code_to_amount.get(code, 0)

            fund_record = {
                "Code": code,
                "Name": name,
                "Money": money,
                "Limit": daily_limit,
                "PurchaseStatus": status,
                "Fee": limit.get("fee"),
                "TotalScore": total_score,
            }
            for label in period_days:
                p = period_data.get(label, {})
                fund_record[f"{label}_Return"] = p.get("cumulative_return")
                fund_record[f"{label}_AnnReturn"] = p.get("annualised_return")
                fund_record[f"{label}_Volatility"] = p.get("volatility")
                fund_record[f"{label}_MaxDrawdown"] = p.get("max_drawdown")
                fund_record[f"{label}_Calmar"] = p.get("calmar")
                fund_record[f"{label}_Sharpe"] = p.get("sharpe")

            # ---- QQQ comparison (new feature) ----
            if qqq_df is not None:
                try:
                    nav_ret = nav_df.copy()
                    nav_ret["return"] = nav_ret["单位净值"].pct_change()
                    nav_ret = nav_ret[["日期", "return"]].dropna()

                    period_metrics = {}
                    for label, days in period_days.items():
                        start_dt = end_date - timedelta(days=days)
                        metrics = compute_period_metrics(
                            nav_ret, qqq_df, start_dt, end_date, label
                        )
                        if metrics is not None:
                            period_metrics.update(metrics)

                    if period_metrics:
                        fund_record.update(period_metrics)
                        # Overall metrics
                        merged_full = pd.merge(
                            nav_ret,
                            qqq_df,
                            left_on="日期",
                            right_on="Date",
                            how="inner",
                        )
                        if len(merged_full) >= 10:
                            fund_ret = merged_full["return"].values
                            qqq_ret = merged_full["QQQ_Return"].values
                            corr = (
                                np.corrcoef(fund_ret, qqq_ret)[0, 1]
                                if len(fund_ret) > 1
                                else 0.0
                            )
                            abs_corr = abs(corr) if not np.isnan(corr) else 0.0
                            win_rate = np.mean(fund_ret > qqq_ret)
                            both_up = (fund_ret > 0) & (qqq_ret > 0)
                            both_down = (fund_ret < 0) & (qqq_ret < 0)
                            same_dir_rate = (np.sum(both_up) + np.sum(both_down)) / len(
                                fund_ret
                            )
                            if len(fund_ret) > 1 and np.std(qqq_ret) > 1e-9:
                                slope, _ = np.polyfit(qqq_ret, fund_ret, 1)
                                beta = slope
                            else:
                                beta = np.nan
                            fund_record["Overall_Correlation"] = corr
                            fund_record["Overall_AbsCorrelation"] = abs_corr
                            fund_record["Overall_WinRate"] = win_rate
                            fund_record["Overall_SameDirectionRate"] = same_dir_rate
                            fund_record["Overall_Beta"] = beta
                            fund_record["Overall_CommonDays"] = len(merged_full)

                        label_1y = "1Y"
                        if f"{label_1y}_Correlation" in fund_record:
                            sharpe_1y = fund_record.get(f"{label_1y}_Sharpe", None)
                            sharpe_str = (
                                f"{sharpe_1y:.3f}" if sharpe_1y is not None else "N/A"
                            )
                            print(
                                f"[{idx+1}/{total}] {code} {name} -> Score={total_score:.6f}  |r|_1Y={fund_record[f'{label_1y}_AbsCorrelation']:.4f}  Win_1Y={fund_record[f'{label_1y}_WinRate']:.2%}  Beta_1Y={fund_record[f'{label_1y}_Beta']:.3f}  Sharpe_1Y={sharpe_str}"
                            )
                        else:
                            print(
                                f"[{idx+1}/{total}] {code} {name} -> Score={total_score:.6f}  (period metrics computed)"
                            )
                    else:
                        print(
                            f"[{idx+1}/{total}] {code} {name} -> Score={total_score:.6f}  Insufficient data for period metrics"
                        )
                        results.append(fund_record)
                        continue
                except Exception as e:
                    print(
                        f"[{idx+1}/{total}] {code} {name} -> Error in QQQ comparison: {e}"
                    )
                    results.append(fund_record)
                    continue
            else:
                print(
                    f"[{idx+1}/{total}] {code} {name} -> Score={total_score:.6f} (no QQQ data)"
                )
                results.append(fund_record)
                continue

            results.append(fund_record)

        except Exception as e:
            print(f"Error on {code}: {e}")

    results.sort(key=lambda x: x.get("TotalScore", 0), reverse=True)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "funds": results,
    }
    with open(SCORE_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✅ JSON saved to {SCORE_JSON}")

    if results:
        best = results[0]
        print(
            f"\nBest fund: {best.get('Name', '')} (score={best.get('TotalScore', 0):.6f})"
        )
        for label in ["1Y", "3Y"]:
            corr_key = f"{label}_AbsCorrelation"
            win_key = f"{label}_WinRate"
            beta_key = f"{label}_Beta"
            sharpe_key = f"{label}_Sharpe"
            if corr_key in best:
                sharpe_val = best.get(sharpe_key, None)
                sharpe_str = f"{sharpe_val:.3f}" if sharpe_val is not None else "N/A"
                print(
                    f"  {label}: |r|={best[corr_key]:.4f}, Win={best.get(win_key, 0):.2%}, Beta={best.get(beta_key, 0):.3f}, Sharpe={sharpe_str}"
                )
        print("Period details (returns & risk):")
        for label in period_days:
            ret_val = best.get(f"{label}_Return")
            ann_val = best.get(f"{label}_AnnReturn")
            cal_val = best.get(f"{label}_Calmar")
            sharpe_val = best.get(f"{label}_Sharpe")

            ret_str = f"{ret_val:.4%}" if ret_val is not None else "N/A"
            ann_str = f"{ann_val:.4%}" if ann_val is not None else "N/A"
            cal_str = f"{cal_val:.3f}" if cal_val is not None else "N/A"
            sharpe_str = f"{sharpe_val:.3f}" if sharpe_val is not None else "N/A"

            print(f"  {label}: cum_ret={ret_str}, ann_ret={ann_str}, calmar={cal_str}, Sharpe={sharpe_str}")
    else:
        print("No valid funds found after filtering.")

    return output


# ------------------------------------------------------------------
# 8. QQQ & PE data generation (unchanged)
# ------------------------------------------------------------------

TICKER = "QQQ"
START_DATE = "2000-01-01"
END_DATE = datetime.today().strftime("%Y-%m-%d")
MAX_RETRIES = 2
RETRY_DELAYS = [30, 60]
INITIAL_CAPITAL = 1000.0


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

    merged = pd.merge_asof(
        qqq, pe, left_index=True, right_on="Date", direction="backward"
    )
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
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": records.to_dict(orient="records"),
        "default_parameters": {
            "initial_capital": INITIAL_CAPITAL,
            "start_date": start,
            "end_date": end,
        },
    }

    with open(QQQ_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print("✅ qqq_pe_data.json saved")


# ------------------------------------------------------------------
# 9. Main
# ------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Generating QQQ & PE data (qqq_pe_data.json) ===")
    generate_qqq_pe_json()

    print("\n=== Generating US fund scores (funds.json) ===")
    generate_funds_json(refresh_cache=False)

    print("\n🎉 All JSON files generated successfully.")