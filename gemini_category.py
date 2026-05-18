import functools
import json
import os
import urllib.error
import urllib.request


def _run_gemini_request(prompt: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    print(os.getenv("GEMINI_API_KEY"))
    if not api_key:
        return ""

    model = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
    endpoint = f"https://generativelanguage.googleapis.com/v1beta2/models/{model}:generate"
    request_data = {
        "input": {"text": prompt},
        "temperature": 0.0,
        "maxOutputTokens": 200,
    }

    try:
        req = urllib.request.Request(
            endpoint,
            method="POST",
            data=json.dumps(request_data).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.load(resp)

        if "candidates" in payload and payload["candidates"]:
            return str(payload["candidates"][0].get("output", ""))

        output = payload.get("output", "")
        if isinstance(output, dict):
            return str(output.get("text", ""))
        return str(output)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError):
        return ""


def _build_batch_prompt(merchant_names):
    prompt_lines = [
        "Assign a single short category for each merchant name below.",
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
    # return ["a"] * len(cleaned)
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
