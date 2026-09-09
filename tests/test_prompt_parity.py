"""The two arms must run on byte-identical instructions.

If they drift, every behavioural comparison between `web-demo/` and `pipecat_demo/`
becomes "which prompt is better tuned" instead of "which architecture behaves how".
This test is the thing that makes the claim in the docs enforceable.
"""
import re
from pathlib import Path

from pipecat_demo.prompts import BASE_INSTRUCTIONS

APP_JS = Path(__file__).resolve().parent.parent / "web-demo" / "static" / "app.js"


def test_prompt_matches_web_demo_verbatim():
    source = APP_JS.read_text(encoding="utf-8")
    match = re.search(r"const BASE_INSTRUCTIONS = `(.*?)`;", source, re.S)
    assert match, "BASE_INSTRUCTIONS not found in web-demo/static/app.js"

    assert match.group(1) == BASE_INSTRUCTIONS, (
        "pipecat_demo/prompts.py has drifted from web-demo/static/app.js. "
        "Update both in the same commit."
    )
