import re

def normalize_transcript_spacing(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

CLAUSE_BOUNDARY_CHARS = frozenset(",.!?;:")

def drain_clause_boundary_phrases(buffer: str) -> tuple[list[str], str]:
    """Extract speakable phrases ending at comma, period, etc. Returns (phrases, remainder)."""
    phrases: list[str] = []
    i = 0
    n = len(buffer)

    while i < n:
        cut = None
        for j in range(i, n):
            ch = buffer[j]
            if ch not in CLAUSE_BOUNDARY_CHARS:
                continue

            if j == n - 1:
                if ch in ".!?":
                    cut = j + 1
                break

            nxt = buffer[j + 1]
            if ch == ",":
                if nxt.isdigit():
                    continue
                if nxt.isspace() or nxt in "\"')]}":
                    cut = j + 1
                    break
            elif nxt.isspace() or nxt in "\"')]}":
                cut = j + 1
                break

        if cut is None:
            break

        phrase = buffer[i:cut]
        if phrase.strip():
            phrases.append(phrase)
        i = cut
        while i < n and buffer[i].isspace():
            i += 1

    return phrases, buffer[i:]

def split_tts_phrases(text: str) -> list[str]:
    """Split full lines into clause-sized TTS phrases (commas, stops, etc.)."""
    normalized = normalize_transcript_spacing(text)
    if not normalized:
        return []
    phrases, remainder = drain_clause_boundary_phrases(normalized)
    if remainder.strip():
        phrases.append(remainder.strip())
    return phrases

def utterance_flush_delay(text: str) -> float:
    normalized = normalize_transcript_spacing(text)
    words = normalized.split()
    word_count = len(words)
    
    ends_with_terminal = normalized.endswith(("?", "!"))
    ends_with_period = normalized.endswith(".")
    
    # Only treat period as a terminal pause if we have more than 2 words (e.g. avoid splitting "Blanco.")
    if ends_with_terminal or (ends_with_period and word_count > 2):
        return 0.60
    return 2.00
