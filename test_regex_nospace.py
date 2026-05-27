import re

# Match punctuation: . ! ? ; : ,
# Not preceded by common abbreviations.
# Followed by optional closing quotes/parens.
# Then followed by:
#  - space \s+
#  - end of string $
#  - uppercase letter or inverted punctuation [A-Z¡¿]
#  - or if the punctuation was ? or !, allow any letter [a-zA-Z¡¿]
# We don't use re.IGNORECASE so [A-Z] only matches uppercase.

BOUNDARY_REGEX = re.compile(
    r'(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bDr)(?<!\bProf)(?<!\bSr)(?<!\bSra)(?<!\bvs)(?<!\betc)'
    r'(?<!\ba\.m)(?<!\bp\.m)(?<!\be\.g)(?<!\bi\.e)'
    r'(?:'
        r'([!?]+[\'\"\)\]}]*)(?=\s|$|[a-zA-Z¡¿])'
        r'|'
        r'([.;:,]+[\'\"\)\]}]*)(?=\s|$|[A-Z¡¿])'
    r')'
)

def drain_clause_boundary_phrases(buffer: str):
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

tests = [
    "Hola?How are you.",
    "Im fine.And you",
    "What?!how could you",
    "google.com is good.",
    "1,000 is a number.",
    "Hello,world",
    "Dr.Smith is here.",
    "Hola?how are you"
]

for t in tests:
    phrases, rem = drain_clause_boundary_phrases(t)
    print(f"Original: {t}")
    print(f"Phrases: {phrases}")
    print(f"Remainder: '{rem}'\n")
