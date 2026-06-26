"""MRV4ULT AI — parse raw WhatsApp watch messages into structured JSON."""

import json
import os
import re
import sys
from pathlib import Path

from openai import OpenAI

PROMPT_PATH = Path(__file__).parent / "prompts" / "parser_prompt.md"
DEFAULT_MODEL = "gpt-4o-mini"


def load_prompt_template() -> str:
    if not PROMPT_PATH.is_file():
        raise FileNotFoundError(f"Parser prompt not found: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


def read_message() -> str:
    print("Paste WhatsApp message (press Enter on an empty line when done):")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            break
        lines.append(line)

    message = "\n".join(lines).strip()
    if not message:
        print("Error: empty message.", file=sys.stderr)
        sys.exit(1)
    return message


def parse_json_response(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def parse_message(message: str, client: OpenAI, model: str) -> dict:
    prompt = load_prompt_template().replace("{{MESSAGE}}", message)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You parse luxury watch WhatsApp messages. Return valid JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    content = response.choices[0].message.content
    if not content:
        raise ValueError("OpenAI returned an empty response.")

    return parse_json_response(content)


def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    message = read_message()
    client = OpenAI(api_key=api_key)

    try:
        result = parse_message(message, client, model)
    except json.JSONDecodeError as exc:
        print(f"Error: model returned invalid JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
