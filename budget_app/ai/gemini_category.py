from typing import Any
from google import genai
import streamlit as st
from pathlib import Path

from budget_app.transactions.categories import CATEGORIES


def _extract_response_text(response: Any) -> str:
    if response is None:
        return ""

    if isinstance(response, str):
        return response

    if hasattr(response, "text") and isinstance(response.text, str):
        return response.text

    if hasattr(response, "output_text") and isinstance(response.output_text, str):
        return response.output_text

    if isinstance(response, dict):
        if "output_text" in response and isinstance(response["output_text"], str):
            return response["output_text"]
        if "candidates" in response and response["candidates"]:
            first = response["candidates"][0]
            if isinstance(first, dict):
                return str(first.get("output", ""))
        if "output" in response:
            output = response["output"]
            if isinstance(output, str):
                return output
            if isinstance(output, dict) and isinstance(output.get("text"), str):
                return output["text"]

    if hasattr(response, "to_dict"):
        try:
            return _extract_response_text(response.to_dict())
        except Exception:
            pass

    if isinstance(response, list) and response:
        return _extract_response_text(response[0])

    return ""


def _run_gemini_request(prompt: str, LOGS_DIR: Path) -> str:
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
    except KeyError:
        print("GEMINI_API_KEY not set")
        return ""

    try:
        model = st.secrets["GEMINI_MODEL"]
    except KeyError:
        print("GEMINI_MODEL not set")
        return ""

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=prompt
        )
        with open(LOGS_DIR / "raw_response.txt", "w", encoding="utf-8") as file:
            file.write(str(response))
        return _extract_response_text(response)
    except Exception as exc:
        print("Gemini SDK error:", exc)
        return ""


def _build_batch_prompt(merchant_names, hints=None):
    prompt_lines = [
        f"Assign a single short category for each merchant name below. Choose the best fit from this exact list: {', '.join(CATEGORIES)}",
        "Some merchants include a hint from the bank's own transaction category. "
        "Treat it as a useful signal, not a strict rule — it uses a different, "
        "more granular category scheme than the list above.",
        "Return only the category names, one per line, in the same order.",
    ]
    for i, merchant in enumerate(merchant_names):
        prompt_lines.append(f"Merchant: {merchant}")
        hint = hints[i] if hints else None
        if hint:
            prompt_lines.append(f"Bank category hint: {hint}")
        prompt_lines.append("Category:")
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

    if unknown:
        prompt = _build_batch_prompt(unknown, [merchant_hint.get(m) for m in unknown])
        with open(LOGS_DIR / "prompt.txt", "w", encoding="utf-8") as file:
            file.write(prompt)

        raw_output = _run_gemini_request(prompt, LOGS_DIR)
        with open(LOGS_DIR / "response.txt", "w", encoding="utf-8") as file:
            file.write(raw_output)
        categories = [line.strip() for line in str(raw_output).splitlines() if line.strip()]

        for merchant, category in zip(unknown, categories):
            result_map[merchant] = category
        for merchant in unknown[len(categories):]:
            result_map[merchant] = ""

    return [result_map.get(m, "") if m else "" for m in cleaned]


def categorize_merchant(merchant: str) -> str:
    return categorize_merchants((merchant,))[0] if merchant is not None else ""
