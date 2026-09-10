"""Prompt-injection scan for third-party MCP tool text.

An MCP server provides names, descriptions and JSON-Schema strings that all get fed to our LLM. A
malicious or compromised one could embed instructions there ("ignore previous instructions",
"exfiltrate the API key"), so we scan before trusting or registering the tool.

The normalizing pre-pass matters more than the pattern list: a model reads a soft-hyphenated "Ig­nore all
instructions" as the plain sentence, while a regex over raw bytes does not, because `\\s` matches
neither the soft hyphen nor the zero-width space. This is a filter, not a boundary — it cannot be
complete, and the real defence is that fetched text is data, never instructions. Keep it cheap."""
from __future__ import annotations

import re
import unicodedata

# Zero-width / formatting characters a model ignores and a regex does not. Cf: soft hyphen, ZWSP, ZWNJ,
# ZWJ, word joiner, BOM, LTR/RTL marks and the bidi overrides.
_INVISIBLE = dict.fromkeys(
    [0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x200E, 0x200F,
     0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069]
)

_PATTERNS = [
    # EN + ES. The separator is [\W_]+, not \W+: `_` is a word char, so a plain \W+ could not cross
    # "ignore-all_previous.instructions" — the same trap the API-key pattern below documents.
    re.compile(r"ignor\w*[\W_]+(all[\W_]+|the[\W_]+)?(previous|prior|above|earlier|anterior\w*)[\W_]+"
               r"(instruction|prompt|message|indicacion|instruccion)", re.I),
    re.compile(r"(disregard|olvida\w*|descarta\w*)\W+(the\W+|el\W+|las\W+)?"
               r"(system|previous|above|sistema|anterior\w*)", re.I),
    # Spanish puts the adjective AFTER the noun, and the pattern above only had the English order —
    # so "ignora las instrucciones anteriores", the way anyone would actually write it, walked through
    # while the unnatural "ignora las anteriores instrucciones" was caught. Nouns are kept narrow on
    # purpose: `orden` and `mensaje` are ordinary words in commerce and chat tool descriptions, and a
    # false positive here REFUSES a legitimate server.
    re.compile(r"(ignor|olvid|descart)\w*[\W_]+"
               r"((todas?|todos?|las|los|el|la|cualquier|mis|tus|sus)[\W_]+){0,3}"
               r"(instruccion|indicacion|directriz|directrices|prompt|contexto|regla)\w*[\W_]+"
               r"(de[\W_]+arriba|anterior|previ[ao]s?|precedente|prior)\w*", re.I),
    # (?:\b|_) not \b: `_` is a word char, so OPENAI_API_KEY / KOTOBA_API_KEY — the actual spelling of
    # every target — had no boundary before "API" and slipped past.
    re.compile(r"\b(reveal|exfiltrate|leak|send|print|dump|envia\w*|manda\w*|revela\w*|filtra\w*)\b"
               r".{0,40}(?:\b|_)(api\W?_?key|token|secret\w*|password|credential\w*|env|clave|contrasena)",
               re.I),
    re.compile(r"\byou\s+are\s+now\b|\beres\s+ahora\b|\bnew\s+(system\s+)?(prompt|role|instructions)\b", re.I),
    re.compile(r"\b(always|never|siempre|nunca)\W+(call|use|invoke|run|llama\w*|usa\w*|ejecuta\w*)"
               r"\W+(this\W+tool|esta\W+herramienta)", re.I),
    # Fake role tags, in the four shapes seen: a tag, a chat-template marker, and a role word alone as a
    # heading or label ("### System", "[SYSTEM]", "SYSTEM:").
    re.compile(r"<\s*/?\s*(system|developer|assistant)\s*>", re.I),
    re.compile(r"<\|\s*im_(start|end)\s*\|>", re.I),
    re.compile(r"(?:^|\n)\s*(?:#{1,6}\s*|\[)\s*(system|developer|assistant)\b", re.I),
    re.compile(r"(?:^|\n)\s*(system|developer|assistant)\s*[\]:]", re.I),
    re.compile(r"\bdo\s+not\s+tell\s+(the\s+)?user\b|\bno\s+(se\s+)?lo\s+digas\s+al\s+usuario\b", re.I),
    re.compile(r"\b(override|ignore|bypass|salta\w*)\b.{0,30}"
               r"\b(safety|security|approval|permission|seguridad|aprobacion|permiso)", re.I),
    # Reading a well-known secret path is never a legitimate tool description.
    re.compile(r"(\.ssh/|id_rsa|id_ed25519|\.aws/credentials|\.netrc|\.keystore_key|\.env\b)", re.I),
    re.compile(r"\b(pre-?approved|ya\s+(esta|fue)\s+aprobad\w+)\b.{0,30}\b(all|action|todo|accion\w*)", re.I),
]

# A description this long is not documentation; it is a place to hide something.
_MAX_LEN = 4000


def normalize(text: str, invisible_as: str = "") -> str:
    """What the MODEL effectively reads: invisibles resolved, compatibility forms folded, accents stripped,
    whitespace collapsed. Patterns are written against THIS, never against the raw string.

    `invisible_as` exists because there is no single right answer. DELETING a zero-width space joins the
    words ("Ignore<ZWSP>previous" becomes one token, which no word-separated pattern matches); turning it
    into a space BREAKS a word split by a soft hyphen ("Ig<SHY>nore" becomes "Ig nore"). A model reads both
    as the plain sentence, so scan_description tries both."""
    t = text or ""
    t = t.translate(dict.fromkeys(_INVISIBLE, invisible_as or None))
    t = unicodedata.normalize("NFKC", t)
    # Strip combining marks so the Spanish patterns match with or without accents.
    t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
    return re.sub(r"[^\S\n]+", " ", t)


def scan_description(text: str) -> str | None:
    """Return a short reason if `text` looks like a prompt-injection attempt, else None (clean)."""
    raw = text or ""
    if len(raw) > _MAX_LEN:
        return f"description too long ({len(raw)} chars)"
    for variant in (normalize(raw, ""), normalize(raw, " ")):
        for pat in _PATTERNS:
            if pat.search(variant):
                return f"matched injection pattern: {pat.pattern[:48]}"
    return None
