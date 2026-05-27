import re

boundary_regex = re.compile(
    r'(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bDr)(?<!\bProf)(?<!\bSr)(?<!\bSra)(?<!\bvs)(?<!\betc)'
    r'(?<!\ba\.m)(?<!\bp\.m)(?<!\be\.g)(?<!\bi\.e)'
    r'([.!?;:,]+[\'\"\)\]}]*\s+)',
    re.IGNORECASE
)

def drain_clause_boundary_phrases(buffer: str):
    phrases = []
    while True:
        match = boundary_regex.search(buffer)
        if not match:
            break
        cut = match.end()
        phrase = buffer[:cut]
        if phrase.strip():
            phrases.append(phrase)
        buffer = buffer[cut:]
    return phrases, buffer

tests = [
    "¡Excelente! Sr. Smith, I love your work. It is... amazing. See you at 8:30 a.m. **tomorrow**.",
    "Hola, ¿cómo estás? Yo muy bien.",
    "He won $1,000,000! Great.",
    "This is a test...  of ellipses?! Yes."
]

for t in tests:
    phrases, rem = drain_clause_boundary_phrases(t)
    print(f"Original: {t}")
    print(f"Phrases: {phrases}")
    print(f"Remainder: '{rem}'\n")
