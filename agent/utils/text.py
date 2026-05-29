import re

def normalize_transcript_spacing(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

# Match punctuation: . ! ? ; : ,
# Not preceded by common abbreviations.
# Followed by optional closing quotes/parens.
# Then followed by:
#  - space \s+
#  - uppercase letter or inverted punctuation [A-Z¡¿]
#  - or if the punctuation was ? or !, allow any letter [a-zA-Z¡¿]
BOUNDARY_REGEX = re.compile(
    r'(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bDr)(?<!\bProf)(?<!\bSr)(?<!\bSra)(?<!\bvs)(?<!\betc)'
    r'(?<!\ba\.m)(?<!\bp\.m)(?<!\be\.g)(?<!\bi\.e)'
    r'(?:'
        r'([!?]+[\'\"\)\]}]*)(?=\s|[a-zA-Z¡¿])'
        r'|'
        r'([.]+[\'\"\)\]}]*)(?=\s|[A-Z¡¿])'
    r')'
)

def drain_clause_boundary_phrases(buffer: str) -> tuple[list[str], str]:
    """Extract speakable phrases ending at comma, period, etc. Returns (phrases, remainder)."""
    phrases = []
    while True:
        match = BOUNDARY_REGEX.search(buffer)
        if not match:
            break
        cut = match.end()
        phrase = buffer[:cut]
        if phrase.strip():
            phrases.append(phrase)
        buffer = buffer[cut:]
    return phrases, buffer

def split_tts_phrases(text: str) -> list[str]:
    """Split full lines into clause-sized TTS phrases (commas, stops, etc.)."""
    normalized = normalize_transcript_spacing(text)
    if not normalized:
        return []
    # append a space so the regex can match the end of the sentence
    phrases, remainder = drain_clause_boundary_phrases(normalized + " ")
    if remainder.strip():
        phrases.append(remainder.strip())
    # Clean up any trailing space we added
    return [p.strip() for p in phrases]

def utterance_flush_delay(text: str) -> float:
    """
    Short coalesce window after Deepgram endpointing (see voice_agent STT settings).
    Deepgram already waits for end-of-speech; this only merges back-to-back finals.
    """
    normalized = normalize_transcript_spacing(text)
    if not normalized:
        return 0.0
    words = normalized.split()
    word_count = len(words)

    ends_with_terminal = normalized.endswith(("?", "!"))
    ends_with_period = normalized.endswith(".")

    if ends_with_terminal or (ends_with_period and word_count > 2):
        return 0.05
    return 0.10
