"""Token counting. Uses tiktoken when installed, else a chars/4 estimate
(the same convention Graphify's own benchmark uses)."""
from __future__ import annotations

import math

_enc = None


def _encoder():
    global _enc
    if _enc is None:
        try:
            import tiktoken

            _enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _enc = False
    return _enc


def count_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _encoder()
    if enc:
        try:
            return len(enc.encode(text, disallowed_special=()))
        except Exception:
            pass
    return max(1, math.ceil(len(text) / 4))


def method() -> str:
    return "tiktoken cl100k_base" if _encoder() else "estimate (chars / 4)"
