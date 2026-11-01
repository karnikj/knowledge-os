"""V1 analýza: Gemini přímo z audia (přepis + analýza v jednom volání).

DOČASNÉ — nahrazuje F1 (přepis po kusech) + F2 (analýza nad textem).
Ponecháno, aby šlo zpracovávat videa do dokončení F1.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from pipeline.config import Vault

try:
    from dotenv import load_dotenv
    load_dotenv(Vault.default().root / ".env")
except ImportError:
    pass

PROMPT_PATH = str(Vault.default().prompts / "transcription_prompt.txt")


def get_gemini_client():
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError(
            "Chyba: Nebyla nalezena proměnná prostředí GEMINI_API_KEY. "
            "Nastavte ji prosím v systému nebo v souboru .env v rootu projektu."
        )
    return genai.Client(api_key=api_key)


def load_prompt() -> str:
    if not os.path.exists(PROMPT_PATH):
        raise FileNotFoundError(f"Chyba: Nebyl nalezen prompt v cestě {PROMPT_PATH}")
    return Path(PROMPT_PATH).read_text(encoding="utf-8")


def analyze_audio(file_path: str, model_name: str = "gemini-2.5-flash") -> str:
    from google.genai import types
    from google.genai.errors import APIError

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Chyba: Audio soubor neexistuje v cestě: {file_path}")
    client = get_gemini_client()
    prompt = load_prompt()

    print(f"Nahrávám {os.path.basename(file_path)} do Gemini File API...")
    uploaded = client.files.upload(file=file_path)
    while True:
        st = client.files.get(name=uploaded.name)
        if st.state.name == "ACTIVE":
            break
        if st.state.name == "FAILED":
            raise APIError("Zpracování audio souboru na straně Gemini API selhalo.")
        time.sleep(5)

    try:
        delay = 5
        for attempt in range(1, 4):
            try:
                resp = client.models.generate_content(
                    model=model_name,
                    contents=[uploaded, prompt],
                    config=types.GenerateContentConfig(temperature=0.0),
                )
                return resp.text
            except Exception as e:  # noqa: BLE001
                print(f"Varování: pokus {attempt}/3 selhal: {e}")
                if attempt == 3:
                    raise
                time.sleep(delay)
                delay *= 2
    finally:
        client.files.delete(name=uploaded.name)
    raise RuntimeError("unreachable")
