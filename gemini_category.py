import functools
import os
from typing import Any

from google import genai


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


def _run_gemini_request(prompt: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set")
        return ""

    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
        with open("raw_response.txt", "w", encoding="utf-8") as file:
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


@functools.lru_cache(maxsize=256)
def categorize_merchants(merchants: tuple):
    cleaned = [str(m or "").strip() for m in merchants]
    if not cleaned:
        return []

    unique_order = []
    seen = {}
    for merchant in cleaned:
        if merchant not in seen:
            seen[merchant] = None
            unique_order.append(merchant)

    non_empty = [m for m in unique_order if m]
    if not non_empty:
        return [""] * len(cleaned)

    prompt = _build_batch_prompt(non_empty)
    with open("prompt.txt", "w", encoding="utf-8") as file:
        file.write(prompt)
        
    # temporary to not run api during development, will read from response.txt if it exists
    if os.path.exists("response.txt"):
        with open("response.txt", "r", encoding="utf-8") as file:
            raw_output = file.read()
    else:
        raw_output = _run_gemini_request(prompt)
        with open("response.txt", "w", encoding="utf-8") as file:
            file.write(raw_output)
    categories = [line.strip() for line in str(raw_output).splitlines() if line.strip()]

    result_map = {}
    for merchant, category in zip(non_empty, categories):
        result_map[merchant] = category
    for merchant in non_empty[len(categories):]:
        result_map[merchant] = ""

    return [result_map.get(m, "") if m else "" for m in cleaned]


def categorize_merchant(merchant: str) -> str:
    return categorize_merchants([merchant])[0] if merchant is not None else ""
