import json
from pathlib import Path

import streamlit as st
from google import genai
from google.genai import types

from budget_app.transactions.categories import CATEGORIES

# Chunk size for a single Gemini call. The old approach sent every unique
# merchant in one request and matched the reply back *by line position* — but
# the model doesn't reliably emit exactly one line per item over a long list
# (observed: 182 merchants in, 177 lines back, finish_reason=STOP, well under
# the token cap — a silent miscount with no API-level signal). Short replies
# blanked the tail; drift mid-reply shifted every later category onto the
# wrong merchant. Smaller chunks plus name-based matching below make a
# miscount cost a few blanks instead of corrupting the rest.
BATCH_SIZE = 40

_RESPONSE_SCHEMA = types.Schema(
    type="ARRAY",
    items=types.Schema(
        type="OBJECT",
        properties={
            "merchant": types.Schema(type="STRING"),
            # enum: the model can't invent a category outside the app's list.
            "category": types.Schema(type="STRING", enum=CATEGORIES),
        },
        required=["merchant", "category"],
    ),
)


def _run_gemini_request(prompt: str, LOGS_DIR: Path) -> list:
    """Return a list of {"merchant": ..., "category": ...} dicts, or [] on any
    failure. Structured output guarantees the shape and a valid category, but
    NOT one entry per merchant — the caller still matches by name."""
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        model = st.secrets["GEMINI_MODEL"]
    except KeyError as exc:
        print("Gemini secret not set:", exc)
        return []

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
            ),
        )
        with open(LOGS_DIR / "raw_response.txt", "w", encoding="utf-8") as file:
            file.write(str(response))

        parsed = json.loads(response.text)
        return parsed if isinstance(parsed, list) else []
    except Exception as exc:
        print("Gemini SDK error:", exc)
        return []


def _build_batch_prompt(merchant_names, hints=None):
    prompt_lines = [
        f"Assign one category to each merchant below, choosing only from: {', '.join(CATEGORIES)}",
        "Return one object per merchant, echoing the merchant name back exactly as given.",
        "Some merchants include a hint from the bank's own transaction category. "
        "Treat it as a useful signal, not a strict rule — it uses a different, "
        "more granular category scheme than the list above.",
        "",
    ]
    for i, merchant in enumerate(merchant_names):
        hint = hints[i] if hints else None
        prompt_lines.append(f"- {merchant}" + (f"  (bank hint: {hint})" if hint else ""))
    return "\n".join(prompt_lines)


def _lookup_known_categories(merchants: list) -> dict:
    """Categories already assigned to these merchants in past transactions, so we
    don't re-ask Gemini for merchants we've already categorized before (this
    matters once transactions arrive incrementally via Plaid sync rather than
    one big CSV batch at a time)."""
    try:
        from budget_app.db.transactions import get_known_categories
        return get_known_categories(merchants)
    except Exception as exc:
        print("Known-category lookup failed:", exc)
        return {}


def _categorize_chunk(merchants: list, hints: list, LOGS_DIR: Path) -> dict:
    """One Gemini call for up to BATCH_SIZE merchants. Results are matched back
    by merchant name (case-insensitively), never by position, so a short or
    over-long reply can only leave merchants uncategorized — it can never
    shift a category onto the wrong merchant."""
    prompt = _build_batch_prompt(merchants, hints)
    with open(LOGS_DIR / "prompt.txt", "w", encoding="utf-8") as file:
        file.write(prompt)

    entries = _run_gemini_request(prompt, LOGS_DIR)
    with open(LOGS_DIR / "response.txt", "w", encoding="utf-8") as file:
        file.write(json.dumps(entries, indent=2))

    by_name = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("merchant", "")).strip().lower()
        category = entry.get("category")
        if name and category in CATEGORIES:
            by_name[name] = category

    return {m: by_name[m.strip().lower()] for m in merchants if m.strip().lower() in by_name}


def categorize_merchants(merchants: tuple, hints: tuple = None):
    """hints, if given, is a parallel sequence to merchants — e.g. Plaid's own
    personal_finance_category, humanized. CSV rows have no such signal and
    pass None. Only used for merchants Gemini actually has to classify;
    already-known merchants skip it entirely."""
    CURRENT_SCRIPT = Path(__file__).resolve()
    PROJECT_ROOT = CURRENT_SCRIPT.parents[2]
    LOGS_DIR = PROJECT_ROOT / "logs"
    LOGS_DIR.mkdir(exist_ok=True)

    cleaned = [str(m or "").strip() for m in merchants]
    if not cleaned:
        return []

    unique_order = []
    seen = set()
    merchant_hint = {}
    for i, merchant in enumerate(cleaned):
        if not merchant:
            continue
        if merchant not in seen:
            seen.add(merchant)
            unique_order.append(merchant)
        if merchant not in merchant_hint and hints and i < len(hints) and hints[i]:
            merchant_hint[merchant] = hints[i]
    if not unique_order:
        return [""] * len(cleaned)

    result_map = _lookup_known_categories(unique_order)
    unknown = [m for m in unique_order if m not in result_map]

    for start in range(0, len(unknown), BATCH_SIZE):
        chunk = unknown[start : start + BATCH_SIZE]
        chunk_hints = [merchant_hint.get(m) for m in chunk]
        result_map.update(_categorize_chunk(chunk, chunk_hints, LOGS_DIR))

    return [result_map.get(m, "") if m else "" for m in cleaned]


def categorize_merchant(merchant: str) -> str:
    return categorize_merchants((merchant,))[0] if merchant is not None else ""
