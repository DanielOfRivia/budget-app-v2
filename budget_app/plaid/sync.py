import hashlib

import pandas as pd
import streamlit as st
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_hosted_link import LinkTokenCreateHostedLink
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.link_token_get_request import LinkTokenGetRequest
from plaid.model.link_token_transactions import LinkTokenTransactions
from plaid.model.products import Products
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from budget_app.ai.gemini_category import categorize_merchants
from budget_app.db.plaid import (
    get_decrypted_access_token,
    list_plaid_items,
    save_plaid_item,
    update_cursor,
    upsert_plaid_account,
)
from budget_app.db.transactions import (
    delete_transactions_by_external_ids,
    insert_transactions_by_account_id,
)
from budget_app.plaid.client import get_plaid_client


def create_hosted_link(owner_email: str) -> tuple[str, str]:
    """Create a Plaid Hosted Link session, returning (link_token, hosted_link_url).

    Hosted Link runs the whole flow on Plaid's own domain rather than in an
    embedded widget. Streamlit custom components render inside a sandboxed
    iframe, which Plaid Link's full-page modal can't reliably open in, and
    OAuth-based banks need top-level navigation anyway. The completed session
    is then read back by polling /link/token/get, so this still needs no
    webhook server.
    """
    client = get_plaid_client()
    # Plaid rejects a client_user_id containing PII (it rejected the raw
    # email directly) — it just needs to be a stable opaque per-user id, so
    # hash it rather than inventing a separate users table for this alone.
    client_user_id = hashlib.sha256(owner_email.encode()).hexdigest()
    request = LinkTokenCreateRequest(
        client_name="Budget App",
        language="en",
        country_codes=[CountryCode("US"), CountryCode("CA")],
        user=LinkTokenCreateRequestUser(client_user_id=client_user_id),
        products=[Products("transactions")],
        hosted_link=LinkTokenCreateHostedLink(),
        # Ask for the full window of back-history Plaid supports, so a newly
        # linked bank backfills prior transactions rather than only tracking
        # activity from the link date forward. Institutions vary in how much
        # they actually return.
        transactions=LinkTokenTransactions(days_requested=730),
    )
    response = client.link_token_create(request)
    return response.link_token, response.hosted_link_url


def _public_token_from_session(session) -> tuple[str | None, str | None]:
    """Pull (public_token, institution_name) out of a finished Link session.
    Plaid can report it under results.item_add_results or on_success, so
    check both rather than assuming one shape."""
    results = session.get("results", None)
    if results is not None:
        for item_result in results.get("item_add_results", []) or []:
            public_token = item_result.get("public_token", None)
            if public_token:
                institution = item_result.get("institution", None)
                name = institution.get("name", None) if institution is not None else None
                return public_token, name

    on_success = session.get("on_success", None)
    if on_success is not None:
        public_token = on_success.get("public_token", None)
        if public_token:
            metadata = on_success.get("metadata", None)
            institution = metadata.get("institution", None) if metadata is not None else None
            name = institution.get("name", None) if institution is not None else None
            return public_token, name

    return None, None


def complete_hosted_link(owner_email: str, link_token: str) -> str | None:
    """Poll a Hosted Link token for a finished session and, if one exists,
    exchange its public_token and store the item. Returns the item_id, or
    None if the user hasn't finished linking yet."""
    client = get_plaid_client()
    response = client.link_token_get(LinkTokenGetRequest(link_token=link_token))

    for session in response.get("link_sessions", []) or []:
        public_token, institution_name = _public_token_from_session(session)
        if public_token:
            return exchange_public_token(owner_email, public_token, institution_name)

    return None


def exchange_public_token(owner_email: str, public_token: str, institution_name: str | None = None) -> str:
    """Exchange a Link public_token for a permanent access_token, store it
    (encrypted), and return the new item_id."""
    client = get_plaid_client()
    response = client.item_public_token_exchange(ItemPublicTokenExchangeRequest(public_token=public_token))
    save_plaid_item(owner_email, response.item_id, response.access_token, institution_name=institution_name)
    return response.item_id


def _account_display_name(account) -> str:
    base = account.official_name or account.name
    mask = getattr(account, "mask", None)
    return f"{base} …{mask}" if mask else base


# Paying off a credit card is a transfer, not spending — counting it would
# inflate every dashboard total. The CSV path already drops these via a
# "THANK YOU" merchant check in normalize.py; Plaid gives us a far more
# reliable signal in personal_finance_category, with the string check kept
# only as a fallback for rows Plaid hasn't classified.
_NON_SPEND_DETAILED = {"LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"}
_NON_SPEND_PRIMARY = {"TRANSFER_IN", "TRANSFER_OUT"}
_NON_SPEND_MERCHANT_MARKERS = ("THANK YOU", "AUTOMATIC PAYMENT")


def _is_non_spend(txn, account) -> bool:
    category = txn.get("personal_finance_category", None)
    primary = category.get("primary", None) if category is not None else None
    detailed = category.get("detailed", None) if category is not None else None

    if detailed in _NON_SPEND_DETAILED or primary in _NON_SPEND_PRIMARY:
        return True

    # A loan payment charged *on a credit card account* is paying down that
    # card. The same category on a chequing account may be a real car or
    # student loan payment worth tracking, so the account type is the
    # deciding context rather than the category alone.
    account_type = str(getattr(account, "type", "") or "").lower()
    if primary == "LOAN_PAYMENTS" and account_type == "credit":
        return True

    merchant = (txn.merchant_name or txn.name or "").upper()
    return any(marker in merchant for marker in _NON_SPEND_MERCHANT_MARKERS)


def _humanize_plaid_category_part(value: str) -> str:
    return value.replace("_", " ").strip().capitalize()


def _category_hint(txn) -> str | None:
    """Turn Plaid's own classification (e.g. FOOD_AND_DRINK /
    FOOD_AND_DRINK_FAST_FOOD) into a readable hint for the Gemini prompt —
    "Food and drink > Fast food". This is real signal Plaid already computed
    for free; feeding it in measurably improves categorization of merchant
    names that are otherwise just noise (verified: an unrecognizable test
    name fell back to "Other" with no hint, "Transport" with one)."""
    category = txn.get("personal_finance_category", None)
    primary = category.get("primary", None) if category is not None else None
    if not primary:
        return None

    detailed = category.get("detailed", None) if category is not None else None
    parts = [_humanize_plaid_category_part(primary)]
    if detailed:
        suffix = detailed[len(primary) + 1 :] if detailed.startswith(primary + "_") else detailed
        cleaned_suffix = _humanize_plaid_category_part(suffix)
        if cleaned_suffix and cleaned_suffix.lower() != parts[0].lower():
            parts.append(cleaned_suffix)
    return " > ".join(parts)


def sync_transactions(owner_email: str, item_id: str) -> dict:
    """Pull new/changed transactions for one linked item via /transactions/sync,
    categorize the new ones through the same Gemini path as CSV uploads, and
    insert them. Returns a summary dict for the UI to report."""
    client = get_plaid_client()
    access_token = get_decrypted_access_token(owner_email, item_id)

    items = list_plaid_items(owner_email)
    item_row = items.loc[items["item_id"] == item_id]
    cursor = item_row["cursor"].iloc[0]
    cursor = cursor if pd.notna(cursor) else None
    institution_name = item_row["institution_name"].iloc[0]

    all_transactions = []
    removed_external_ids = []
    accounts_by_id = {}
    has_more = True
    while has_more:
        # The cursor field must be omitted entirely on a first sync — the SDK
        # rejects cursor=None outright, and Plaid reads a missing cursor as
        # "start from the beginning".
        request = (
            TransactionsSyncRequest(access_token=access_token, cursor=cursor)
            if cursor
            else TransactionsSyncRequest(access_token=access_token)
        )
        response = client.transactions_sync(request)
        for account in response.accounts:
            accounts_by_id[account.account_id] = account
        all_transactions.extend(response.added)
        # `modified` is folded in with `added` here — insert_transactions_by_account_id
        # dedups on (account_id, external_id), so a modified row just re-inserts
        # under the same external_id and is silently skipped as a duplicate.
        # Real update-in-place would need an UPDATE path; deferred, since Plaid
        # modifications are typically pending->posted amount/date tweaks, not
        # something this app needs to reconcile precisely yet.
        all_transactions.extend(response.modified)
        removed_external_ids.extend(t.transaction_id for t in response.removed)
        cursor = response.next_cursor
        has_more = response.has_more

    update_cursor(owner_email, item_id, cursor)

    removed_count = delete_transactions_by_external_ids(owner_email, removed_external_ids)

    if not all_transactions:
        return {"inserted": 0, "skipped": 0, "filtered": 0, "removed": removed_count, "accounts_linked": 0}

    account_id_map = {
        plaid_account_id: upsert_plaid_account(
            owner_email, item_id, plaid_account_id, _account_display_name(account), institution_name
        )
        for plaid_account_id, account in accounts_by_id.items()
    }

    spend_transactions = [
        txn for txn in all_transactions if not _is_non_spend(txn, accounts_by_id.get(txn.account_id))
    ]
    filtered_out = len(all_transactions) - len(spend_transactions)

    if not spend_transactions:
        return {
            "inserted": 0,
            "skipped": 0,
            "filtered": filtered_out,
            "removed": removed_count,
            "accounts_linked": len(account_id_map),
        }

    category_hints = [_category_hint(txn) for txn in spend_transactions]
    rows_df = pd.DataFrame(
        {
            "date": txn.date,
            "merchant": txn.merchant_name or txn.name,
            "amount": float(txn.amount),
            "account_id": account_id_map.get(txn.account_id),
            "external_id": txn.transaction_id,
        }
        for txn in spend_transactions
    )

    with st.spinner("Categorizing new transactions…"):
        rows_df["category"] = categorize_merchants(tuple(rows_df["merchant"]), hints=tuple(category_hints))
    rows_df["notes"] = ""
    rows_df["source"] = "plaid"
    rows_df["source_file"] = None

    result = insert_transactions_by_account_id(rows_df.to_dict("records"))
    return {
        "inserted": result["inserted"],
        "skipped": result["skipped"],
        "filtered": filtered_out,
        "removed": removed_count,
        "accounts_linked": len(account_id_map),
    }
