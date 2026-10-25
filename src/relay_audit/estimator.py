"""Screening heuristic, not a tokenizer: CJK=1, other runs=ceil(chars/4)."""
import math


def is_cjk(char):
    n = ord(char)
    return (0x3400 <= n <= 0x4DBF or 0x4E00 <= n <= 0x9FFF
            or 0xF900 <= n <= 0xFAFF or 0x20000 <= n <= 0x323AF
            or 0x3040 <= n <= 0x30FF or 0xAC00 <= n <= 0xD7AF)


def estimate_tokens(text):
    total = run = 0
    for char in text:
        if char.isspace() or is_cjk(char):
            total += math.ceil(run / 4)
            run = 0
            if is_cjk(char):
                total += 1
        else:
            run += 1
    return total + math.ceil(run / 4)
