"""Shared LLM client helper for ChangeProof agentic reasoning calls.

Implements fallback chain:
  1. Google Gemini (gemini-3.5-flash / gemini-3.5-flash-lite / gemini-3.6-flash / gemini-3.7-flash) with 429 retry
  2. OpenAI (gpt-4o-mini)
  3. Anthropic (claude-3-5-haiku-latest)
  4. Returns None (caller falls back to deterministic/template logic)

All calls use temperature=0.0 for deterministic structured-output prompts.
"""
import os
import re
import json
import time
from typing import Optional


def _load_env_if_needed():
    """Loads .env from project root or current working directory if keys not in environ."""
    for candidate in [".env", os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")]:
        if os.path.exists(candidate):
            for enc in ["utf-8-sig", "utf-8", "utf-16"]:
                try:
                    with open(candidate, "r", encoding=enc) as f:
                        for line in f.read().splitlines():
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                k, v = line.split("=", 1)
                                if k.strip() not in os.environ:
                                    os.environ[k.strip()] = v.strip()
                    break
                except Exception:
                    continue


def call_llm(prompt: str, max_tokens: int = 2048) -> Optional[str]:
    """Makes a single LLM call with the given prompt following the fallback chain.

    Args:
        prompt: The full user prompt.
        max_tokens: Maximum response tokens.

    Returns:
        Response text string, or None if no provider succeeded.
    """
    _load_env_if_needed()

    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")

    # ------------------------------------------------------------------
    # 1. Google Gemini Provider
    # ------------------------------------------------------------------
    if gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)

            candidate_models = [
                "gemini-3.5-flash",
                "gemini-3.5-flash-lite",
                "gemini-3.6-flash",
                "gemini-3.7-flash",
                "gemini-flash-latest",
                "gemini-pro-latest",
            ]

            for model_name in candidate_models:
                for attempt in range(2):  # 1 initial + 1 retry on 429
                    try:
                        model = genai.GenerativeModel(model_name)
                        resp = model.generate_content(
                            prompt,
                            generation_config={"temperature": 0.0, "max_output_tokens": max_tokens},
                        )
                        if resp and resp.text:
                            return resp.text
                    except Exception as ge:
                        err_str = str(ge).lower()
                        is_rate_limit = "429" in err_str or "quota" in err_str or "rate" in err_str or "resourceexhausted" in err_str
                        if is_rate_limit and attempt == 0:
                            print(f"[LLM] Gemini ({model_name}) rate limited (429/quota), retrying once after 1.5s...")
                            time.sleep(1.5)
                            continue
                        elif "not found" in err_str or "404" in err_str or "no longer available" in err_str:
                            break
                        else:
                            print(f"[LLM] Gemini ({model_name}) error: {ge}. Trying next model...")
                            break
        except Exception as e:
            print(f"[LLM] Gemini provider initialization failed: {e}. Falling through...")

    # ------------------------------------------------------------------
    # 2. OpenAI Provider
    # ------------------------------------------------------------------
    if openai_key:
        try:
            import openai
            oa_client = openai.OpenAI(api_key=openai_key)
            oa_resp = oa_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=max_tokens,
            )
            if oa_resp.choices and oa_resp.choices[0].message.content:
                return oa_resp.choices[0].message.content
        except Exception as e:
            print(f"[LLM] OpenAI provider error: {e}. Falling through...")

    # ------------------------------------------------------------------
    # 3. Anthropic Provider
    # ------------------------------------------------------------------
    if anthropic_key:
        try:
            import anthropic
            anth_client = anthropic.Anthropic(api_key=anthropic_key)
            anth_resp = anth_client.messages.create(
                model="claude-3-5-haiku-latest",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            if anth_resp.content:
                content_block = anth_resp.content[0]
                return str(getattr(content_block, "text", ""))
        except Exception as e:
            print(f"[LLM] Anthropic provider error: {e}. Falling through...")

    return None


def parse_json_response(response_text: str) -> dict:
    """Strips markdown code fences and parses JSON from an LLM response."""
    clean = response_text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\n?", "", clean)
        clean = re.sub(r"\n?```$", "", clean)
    try:
        return json.loads(clean.strip())
    except Exception:
        return {}
