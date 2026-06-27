import pandas as pd

TRANSACTION_SCHEMA = [
    "date",
    "merchant",
    "amount",
    "account",
    "category",
    "source_file",
    "notes",
]

Transaction = {
    "date": pd.Timestamp,
    "merchant": str,
    "amount": float,   # negative = expense, positive = inflow
    "account": str,
    "notes": str,
    "category": str,
    "source_file": str,
}


def normalize_amount(amount_series):
    if amount_series is not None:
        cleaned = (
            amount_series.astype(str)
            .str.replace("[,\$]", "", regex=True)
            .str.replace("\((.*)\)", r"-\1", regex=True)
        )
        return pd.to_numeric(cleaned, errors="coerce")
    return pd.Series(dtype="float64")


def safe_text(series, length=None):
    if series is None:
        length = 0 if length is None else length
        return pd.Series([""] * length, dtype="string")
    cleaned = series.astype(str).fillna("")
    if length is not None and len(cleaned) != length:
        return pd.Series([cleaned.iloc[i] if i < len(cleaned) else "" for i in range(length)], dtype="string")
    return cleaned


def parse_date(series, length=None):
    if series is None:
        length = 0 if length is None else length
        return pd.Series([pd.NaT] * length, dtype="datetime64[ns]")
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    if length is not None and len(parsed) != length:
        return pd.Series([parsed.iloc[i] if i < len(parsed) else pd.NaT for i in range(length)], dtype="datetime64[ns]")
    return parsed


def detect_source(df):
    if "Account Number" in df.columns:
        return "RBC"
    return "AMEX"


def build_transaction_frame(df, source_file, source_type, account_override=None):
    raw = df.copy()
    length = len(raw)

    date_series = raw.get("Transaction Date")
    if date_series is None:
        date_series = raw.get("Date")
    if date_series is None:
        raise ValueError("Date column not found in uploaded CSV file")
    date_parsed = parse_date(date_series, length=length)

    merchant_series = raw.get("Merchant")
    if merchant_series is None:
        merchant_series = raw.get("Description 1")
    if merchant_series is None:
        merchant_series = raw.get("Description")
    if merchant_series is None:
        merchant_series = raw.get("Description 2")
    if merchant_series is None:
        raise ValueError("Merchant column not found in uploaded CSV file")

    amount_series = None
    if "Amount" in raw.columns:
        amount_series = raw["Amount"]
    elif "CAD$" in raw.columns:
        amount_series = raw["CAD$"]
    else:
        raise ValueError("Amount column not found in uploaded CSV file")

    
    merchant_clean = safe_text(merchant_series, length=length)
    amount_parsed = normalize_amount(amount_series)
    if source_type == "RBC":
        amount_parsed = -amount_parsed

    new_source_file_name = f"{source_type}_{date_parsed.max().strftime('%Y%m%d')}_{date_parsed.min().strftime('%Y%m%d')}.csv"
    account_value = account_override if account_override is not None else source_type
    parsed = pd.DataFrame(
        {
            "date": date_parsed,
            "merchant": merchant_clean,
            "amount": amount_parsed,
            "account": pd.Series([account_value] * length, dtype="string"),
            "category": pd.Series([""] * length, dtype="string"), #category_series,
            "notes": pd.Series([""] * length, dtype="string"),
            "source_file": pd.Series([new_source_file_name] * length, dtype="string"),
        }
    )
    parsed = parsed[TRANSACTION_SCHEMA]
    #remove credit card deposits
    parsed = parsed[~((parsed["amount"] < 0) & parsed["merchant"].str.contains("THANK YOU", case=False, na=False))]

    parsed = parsed.reset_index(drop=True)
    return parsed
