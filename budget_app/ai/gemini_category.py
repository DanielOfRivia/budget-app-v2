from typing import Any
from google import genai
import streamlit as st
from pathlib import Path


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


def _build_batch_prompt(merchant_names):
    prompt_lines = [
        "Assign a single short category for each merchant name below. Choose the best fit from this exact list: Groceries, Transport, Eating out, Health & Wellness, Fun stuff, Gifts, Travel, Clothes, Charity, Other",
        "Return only the category names, one per line, in the same order.",
    ]
    for merchant in merchant_names:
        prompt_lines.append(f"Merchant: {merchant}")
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


def categorize_merchants(merchants: tuple):
    CURRENT_SCRIPT = Path(__file__).resolve()
    PROJECT_ROOT = CURRENT_SCRIPT.parents[2]
    LOGS_DIR = PROJECT_ROOT / "logs"
    LOGS_DIR.mkdir(exist_ok=True)

    cleaned = [str(m or "").strip() for m in merchants]
    if not cleaned:
        return []

    unique_order = []
    seen = set()
    for merchant in cleaned:
        if merchant and merchant not in seen:
            seen.add(merchant)
            unique_order.append(merchant)
    if not unique_order:
        return [""] * len(cleaned)

    result_map = _lookup_known_categories(unique_order)
    unknown = [m for m in unique_order if m not in result_map]

    if unknown:
        prompt = _build_batch_prompt(unknown)
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
