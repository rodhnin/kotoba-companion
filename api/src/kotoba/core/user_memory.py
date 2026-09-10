"""Kotoba's curated long-term memory as STRUCTURED markdown: a small INDEX plus one file per topic.

Layout, jailed under KOTOBA_MEMORY_DIR (default ~/.kotoba/memory, OUTSIDE the repo):
    USER.md          the index — `## Recent` (last N facts, always in the prompt) + `## Topics`
    topics/<slug>.md the full detail per topic, free to grow without bloating the prompt.
The conversation log and full-text search stay in SQLite/FTS5; this module owns the facts.

Every judgement here is biased toward KEEPING, since retiring is a permanent delete with no archive:
a wrongly kept fact is visible clutter, a wrongly dropped one is invisible and self-sealing — the
caller has already told the user it was remembered, and nothing retries."""
from __future__ import annotations

from contextlib import contextmanager
import os
import re
import threading
import unicodedata
from pathlib import Path

from kotoba.core import atomic_file
from kotoba.paths import home_dir

_lock = threading.RLock()

RECENT_CAP = 25
_DEFAULT_TOPIC = "general"
# One fact is one line in a bounded prompt; far past this is a paragraph the extractor failed to split.
_MAX_FACT_CHARS = 400
_MAX_TOPICS_IN_SUMMARY = 12
# How many cross-store hits `recall` adds beside a topic's own facts — see recall() for the measurement.
_RELATED_CAP = 4
# How far a rewording may drift, and nothing more. Overlap alone cannot tell a rewording from a sibling
# fact — measured across a corpus of both, no threshold separates them, because "prefers jasmine tea"
# and "likes jasmine tea" score exactly what "has a cat named Luna" and "has a dog named Luna" score.
# `_distinct` decides that; this only bounds the search.
_DUP_JACCARD = 0.5

# EN + ES noise words (facts arrive in both languages) — kept conservative so distinct facts stay distinct.
_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "user", "users", "user's", "i", "im",
    "my", "me", "to", "of", "and", "has", "have", "his", "her", "their", "that", "it",
    "for", "on", "in", "with", "at", "by", "as", "this", "they", "them", "you", "your",
    "into", "from", "but", "or", "not", "do", "does", "did", "be", "been", "being",
    "will", "would", "can", "could", "also", "just", "any", "some", "often", "currently",
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "con", "para",
    "por", "que", "su", "sus", "y", "o", "en", "se", "es", "está", "esta",
}


def _normalize(s: str) -> str:
    """Lowercase + strip accents so 'Jesús' and 'Jesus' compare equal (the old regex split 'jesús' into
    'jes'/'s', so accented names never matched and duplicated forever)."""
    nfkd = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))

_INDEX_HEADER = """# USER.md — index of what Kotoba remembers

Durable memory, organized by topic — about you and about anything important (projects, plans, context).
Kotoba reads this index every conversation; details live in the linked topic files (she can recall any
of them on demand) and she creates new topics freely as things come up. You can edit any of these.
"""


def memory_dir() -> Path:
    return Path(os.getenv("KOTOBA_MEMORY_DIR", str(home_dir() / "memory"))).expanduser()


def user_md_path() -> Path:
    return memory_dir() / "USER.md"


def topics_dir() -> Path:
    return memory_dir() / "topics"


def slugify(topic: str) -> str:
    """A safe topic filename: lowercase kebab, [a-z0-9-] only — can NEVER escape topics/ (no dots/slashes)."""
    s = re.sub(r"[^a-z0-9]+", "-", (topic or "").lower()).strip("-")
    return s or _DEFAULT_TOPIC


def topic_path(topic: str) -> Path:
    p = (topics_dir() / f"{slugify(topic)}.md").resolve()
    root = topics_dir().resolve()
    if p != root and root not in p.parents:  # defense-in-depth: stay inside topics/
        raise ValueError("topic path escaped the memory jail")
    return p


def _tokens(fact: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _normalize(fact))


def _kw(words: list[str]) -> frozenset[str]:
    return frozenset(w for w in words if w not in _STOP and len(w) > 1)


def _keywords(fact: str) -> frozenset[str]:
    return _kw(_tokens(fact))


_NEG = {"no", "not", "never", "nunca", "jamas", "sin", "dont", "doesnt", "didnt", "isnt", "cant", "wont"}


def _negated(fact: str) -> bool:
    return bool(_NEG & set(re.findall(r"[a-z0-9]+", _normalize(fact))))


_NUMBER_WORDS = frozenset(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred "
    "thousand million billion trillion first second third fourth fifth sixth seventh eighth ninth tenth "
    "eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth "
    "twentieth thirtieth fortieth fiftieth "
    "cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciseis "
    "diecisiete dieciocho diecinueve veinte veintiuno veintidos veintitres veinticuatro veinticinco "
    "veintiseis veintisiete veintiocho veintinueve treinta cuarenta cincuenta sesenta setenta ochenta "
    "noventa cien ciento doscientos trescientos cuatrocientos quinientos seiscientos setecientos "
    "ochocientos novecientos mil millon millones billon primero segundo tercero cuarto quinto sexto "
    "septimo octavo noveno decimo primer tercer primera segunda tercera cuarta quinta sexta septima "
    "octava novena decima".split()
)
_MONTHS = frozenset(
    "january february march april may june july august september october november december "
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split()
)
_WEEKDAYS = frozenset(
    "monday tuesday wednesday thursday friday saturday sunday "
    "lunes martes miercoles jueves viernes sabado domingo".split()
)


def _value_tokens(fact: str) -> dict[str, str]:
    """The tokens that carry a fact's VALUE, mapped to a coarse class: digit-bearing tokens and number
    words ('number'), month names ('month'), weekday names ('weekday'), and mid-sentence Capitalized
    words ('name' — proper nouns). Position 0 is excluded from the name class not just because every
    sentence starts capitalized, but because the store's canonical facts are verb-initial third-person
    English ('Prefers jasmine tea', 'Building Aurora'): counting position 0 turned every verb-swap
    rewording into a 'correction' that beat the duplicate verdict. The price is that a subject-fronted
    correction ('Madrid is where Marco lives' → 'Barcelona …') is invisible here. Computed from the RAW
    fact, not from _keywords: single digits
    ('is 3 years old') fall through the keyword length filter entirely, which is how a '3 → 4'
    correction scored Jaccard 1.00."""
    out: dict[str, str] = {}
    for i, w in enumerate(re.findall(r"[^\W_]+", fact or "")):
        t = _normalize(w)
        if any(c.isdigit() for c in t) or t in _NUMBER_WORDS:
            out[t] = "number"
        elif t in _MONTHS:
            out[t] = "month"
        elif t in _WEEKDAYS:
            out[t] = "weekday"
        elif i > 0 and w[:1].isupper() and len(t) > 1 and t not in _STOP:
            out[t] = "name"
    return out


def _subject_rest(fact: str) -> tuple[frozenset[str], frozenset[str]]:
    """What a fact is ABOUT, and what it then says — split at the first known verb, the same reading
    `_attribute_parts` uses for scope. A fact with no known verb is the store's own canonical shape,
    which opens on the verb: its subject is the person the store belongs to, so it is empty here."""
    toks = _tokens(fact)
    v = next((i for i, t in enumerate(toks) if t in _VERBS), None)
    if v is not None:
        return _kw(toks[:v]), _kw(toks[v + 1:])
    kws = [t for t in toks if t not in _STOP and len(t) > 1]
    return frozenset(), frozenset(kws[1:])


def _distinct(fact: str, old: str) -> bool:
    """A fact about a different subject, built on a different frame, or carrying a value of another
    class is ANOTHER fact: never a duplicate, never a correction, never superseded.

    Three defects wore the same shape: two friends the same age and one lost the other, a sister's
    address retired the user's own as a "refinement", and different facts were refused as repeats. What
    separates them is not how much two overlap but WHERE they differ — a swapped verb is a rewording, a
    swapped subject or frame noun is another statement. A subject word counts only when the other fact
    lacks it ENTIRELY: Spanish puts the subject after the verb often enough that comparing the two as
    sets split one fact in two."""
    new_subject, new_rest = _subject_rest(fact)
    old_subject, old_rest = _subject_rest(old)
    if (new_subject - _keywords(old)) or (old_subject - _keywords(fact)):
        return True
    nv, ov = _value_tokens(fact), _value_tokens(old)
    values = set(nv) | set(ov)
    new_frame, old_frame = new_rest - values, old_rest - values
    if (new_frame - old_frame) and (old_frame - new_frame):
        return True
    new_only = {c for t, c in nv.items() if t not in ov}
    old_only = {c for t, c in ov.items() if t not in nv}
    return bool(new_only and old_only and not (new_only & old_only))


def _corrects(fact: str, old: str) -> bool:
    """True when `fact` reads as the SAME statement as `old` with its value swapped: each side holds a
    value token the other lacks with at least one swapped pair sharing a class, AND the frame — the
    keywords left once every value token EITHER side holds is removed — is identical. Without the frame
    condition any same-class swap above the closeness threshold retired bystander facts ('the dog is 4
    years old' retired the cat's age). The frame strips the union of both sides' values, not each side's
    own, so a token one side capitalizes and the other does not ('marco lives in Madrid' vs 'Marco lives
    in Barcelona') cannot flip the verdict on casing alone. Only consulted for pairs the closeness test
    already called duplicates — below that threshold every verdict is unchanged."""
    if _distinct(fact, old):
        return False
    nv, ov = _value_tokens(fact), _value_tokens(old)
    new_only = {c for t, c in nv.items() if t not in ov}
    old_only = {c for t, c in ov.items() if t not in nv}
    if not (new_only & old_only):
        return False
    values = set(nv) | set(ov)
    return _keywords(fact) - values == _keywords(old) - values


# Superlative-possessive markers, EN + ES, normalized to one key so "favorite color" and "color
# favorito" name the same attribute. The only family with live evidence; extend from evidence, not taste.
_ATTR_MARKERS = {
    "favorite": "favorite", "favourite": "favorite", "favorites": "favorite", "favourites": "favorite",
    "favorito": "favorite", "favorita": "favorite", "favoritos": "favorite", "favoritas": "favorite",
    "preferido": "favorite", "preferida": "favorite", "preferidos": "favorite", "preferidas": "favorite",
    "preferred": "favorite",
}
_COORD = {"and", "y"}
_COPULA = {"is", "are", "was", "were", "es", "son", "era", "eran", "fue", "fueron", "sera", "seran"}
_VERBS = frozenset(_COPULA) | {"has", "have", "likes", "prefers", "wants", "lives", "works",
                               "tiene", "tienen", "vive", "trabaja", "quiere", "prefiere", "gusta"}
_QUOTED = re.compile(r"[\"“”]([^\"“”]{0,160})[\"“”]")
_CLAUSE_HEADS = {
    "they", "he", "she", "it", "we", "you", "i", "their", "his", "her", "its", "my", "our", "your",
    "the", "a", "an", "su", "sus", "el", "la", "los", "las", "un", "una", "le", "les",
    "has", "have", "is", "are", "was", "were", "likes", "prefers", "wants", "lives", "works",
    "tiene", "tienen", "es", "son", "vive", "trabaja", "quiere", "prefiere", "gusta",
}


def unquoted(fact: str) -> str:
    """The fact minus any double-quoted span. A title is not a clause and carries neither the fact's
    grammar nor its language — she stores report names like “Atrapa la fruta …” verbatim. Apostrophes
    are NOT quote marks here: "the user's" and "the user’s" are half the store."""
    return _QUOTED.sub(" ", fact or "")


def is_compound(fact: str) -> bool:
    """True when the text carries more than one statement: a semicolon, or a coordinator opening a new
    clause. A coordinator counts only when a _CLAUSE_HEADS token follows it — followed by a plain noun
    ("salt and pepper", "fish and chips") it is one statement. Used to refuse a compound at the write
    path and to refuse RETIRING one here — a compound fact's other half is a real memory the correction
    knows nothing about."""
    fact = unquoted(fact)
    if ";" in fact:
        return True
    toks = _tokens(fact)
    return any(t in _COORD and i + 1 < len(toks) and toks[i + 1] in _CLAUSE_HEADS
               for i, t in enumerate(toks))


def _attribute(fact: str) -> tuple[str, int, int] | None:
    """(key, first_token_index, last_token_index) of a single-valued attribute phrase, or None. The head
    noun is the adjacent non-stopword — after the marker in English, before it in Spanish. A PLURAL head
    ('favorite colors', 'colores favoritos') states a set, not one value, so it is not an attribute here:
    someone may keep several favourite colours and none of them should retire another."""
    toks = _tokens(fact)
    for i, t in enumerate(toks):
        if t not in _ATTR_MARKERS:
            continue
        for j in (i + 1, i - 1):
            if not 0 <= j < len(toks):
                continue
            head = toks[j]
            if head in _STOP or len(head) < 2:
                continue
            if head.endswith("s"):
                return None
            return f"{_ATTR_MARKERS[t]} {head}", min(i, j), max(i, j)
    return None


def _names(fact: str) -> frozenset[str]:
    return frozenset(t for t, c in _value_tokens(fact).items() if c == "name")


def _attribute_parts(fact: str) -> tuple[str, frozenset[str], frozenset[str]] | None:
    """(key, SCOPE, value) of a single-valued attribute statement, or None when it is not one.

    The COPULA is the boundary, because it is the one place both languages agree: everything before it
    other than the attribute phrase says what the attribute is ABOUT (owner, room, time of day), and
    only what follows it is the VALUE. Reading the scope as the keywords BEFORE the phrase was an
    over-retire — canonical phrasing puts the owner AFTER the head noun ("favorite color of the user's
    daughter"), so it landed in the value set and a kitchen wall retired the daughter's colour. With no
    copula the two regions cannot be told apart, so we abstain: leaving a duplicate is recoverable,
    deleting a third party's memory is not."""
    a = _attribute(fact)
    if not a:
        return None
    key, i, j = a
    toks = _tokens(fact)
    c = next((k for k in range(j + 1, len(toks)) if toks[k] in _COPULA), None)
    if c is None:
        return None
    value = _kw(toks[c + 1:])
    if not value:
        return None
    return key, _kw(toks[:i]) | _kw(toks[j + 1:c]), value


def _attribute_match(fact: str, old: str) -> str | None:
    """"correct" when `old` states the same single-valued attribute, about the same subject and in the
    same context, with a different value, and can be retired for `fact`; "blocked" when it states it but
    must be kept (it is compound, or a proper noun leaves the subject in doubt); None when the two are
    unrelated.

    A differing SCOPE usually means a different fact, not a contradiction — the daughter's colour, the
    kitchen's, the morning's tea — and saying nothing is the honest answer. It is only reported when the
    difference carries a proper NAME, because a named owner may BE the default subject: a store can
    hold "El color favorito de Marco es el verde musgo" next to a bare "Has color favorito is cobalt
    blue", one person written both ways. Neither branch retires anything."""
    a, b = _attribute_parts(fact), _attribute_parts(old)
    if not a or not b or a[0] != b[0] or _negated(fact) != _negated(old) or is_compound(fact):
        return None
    if a[1] != b[1]:
        return "blocked" if (a[1] ^ b[1]) & (_names(fact) | _names(old)) else None
    vn, vo = a[2], b[2]
    # vn < vo is the SAME value with less detail ("blue" vs "deep cobalt blue, for the studio walls"),
    # never a new one: as a correction it let a vaguer rewording delete the fact that held the detail.
    if vn == vo or vn < vo:
        return None
    return "blocked" if (is_compound(old) or (_names(old) - set(_value_tokens(fact)))) else "correct"


_SV_VERBS = frozenset({"lives", "vive"})


_SELF_REF = frozenset({"the", "user", "users", "he", "she", "they", "his", "her", "their",
                       "el", "la", "usuario", "usuaria", "su"})


def _value_shape(fact: str, values: set[str]) -> tuple[str, ...]:
    """The fact's whole token sequence with every value token collapsed to a placeholder, so two
    statements can be compared for the SHAPE their values sit in and not just the words left over.

    A keyword frame cannot see this: stopwords are gone and value tokens are gone too, so "Lives in
    Barcelona with Ana" and "Lives in Madrid" reduce to the same one-word frame and the stored fact's
    other half — who they live WITH — would be deleted with the address. Adjacent values collapse to
    one placeholder, since a place may be spelled in several words without being a different shape.
    A LEADING self-reference is dropped first ("The user lives in…" vs "Lives in…"), or the same move
    is stored twice; only the head is stripped, so "Sister lives in Valencia" stays a different shape."""
    out: list[str] = []
    for t in _tokens(fact):
        t = "*" if t in values else t
        if t != "*" or not out or out[-1] != "*":
            out.append(t)
    while out and out[0] in _SELF_REF:
        out.pop(0)
    return tuple(out)


def _sv_verb_match(fact: str, old: str) -> str | None:
    """The attribute rule's verb-form sibling: "correct" when `old` states the same single-valued verb
    fact (today: where the user LIVES) with the place swapped, "blocked" when it must be kept, None
    when unrelated. This rule works INSIDE a language and never across it, so an English "Lives in
    Madrid" leaves a legacy "Vive en Barcelona" standing — a miss, the recoverable half.

    Guards, all of them the same permanent-delete caution: the frame must be EXACTLY the verb, the
    swap proper-noun-for-proper-noun in BOTH directions (a vaguer restatement whose places are a
    subset corrects nothing), a COMPOUND stored fact is kept and reported, and the value SHAPE must
    match — without it "Lives in Madrid" retired "Lives in Barcelona with Ana". The known limit: no
    geography lives here, so "Lives in Spain" retires "Lives in Madrid" and "Lives in Notion" is a home."""
    if _negated(fact) != _negated(old) or is_compound(fact):
        return None
    nv, ov = _value_tokens(fact), _value_tokens(old)
    new_names = frozenset(t for t, c in nv.items() if c == "name")
    old_names = frozenset(t for t, c in ov.items() if c == "name")
    if not (new_names - old_names) or not (old_names - new_names):
        return None
    values = set(nv) | set(ov)
    frame = _keywords(fact) - values
    if frame != _keywords(old) - values or len(frame) != 1 or next(iter(frame)) not in _SV_VERBS:
        return None
    if _value_shape(fact, values) != _value_shape(old, values):
        return None
    return "blocked" if is_compound(old) else "correct"


def _restates(fact: str, old: str) -> bool:
    """True when `old` already states the SAME attribute, scope and VALUE in different words — the
    reworded twin of a verbatim repeat. It has to outrank the correction override: the live store held
    "Has color favorito is cobalt blue", so saving "Favorite color is cobalt blue" corrected four stale
    colours and, on that alone, was written as a fifth bullet saying what the store already said."""
    a, b = _attribute_parts(fact), _attribute_parts(old)
    return bool(a and b and a == b and _negated(fact) == _negated(old))


def _near_duplicate(nk: frozenset[str], ek: frozenset[str]) -> bool:
    """The raw closeness test applied before any exception: a keyword subset, or overlap at
    _DUP_JACCARD. A proper superset is a refinement (handled by `supersedes`), never a duplicate."""
    if nk <= ek:
        return True
    if ek < nk:
        return False
    return len(nk & ek) / len(nk | ek) >= _DUP_JACCARD


def _bullets(text: str) -> list[str]:
    return [
        ln.strip()[2:].strip()
        for ln in text.splitlines()
        if ln.strip().startswith("- ") and ln.strip()[2:].strip()
    ]


def _duplicate_of(fact: str, existing: list[str]) -> str | None:
    """The stored fact this one is a duplicate OF (None → it deserves writing): its keywords are a
    SUBSET of an existing one, or overlap it by at least _DUP_JACCARD. Accent/stopword-normalized.

    A SUPERSET is not a duplicate — "works as a security engineer at Acme in Madrid" adds real
    information — and treating it as one silently discarded every refinement while the caller reported
    "already remembered". Neither is a CORRECTION: the write path stores it and retires the stale
    version. A correction target ANYWHERE in the store outranks a near-duplicate met earlier, or the
    dog's correction would be dropped on store order alone. A verbatim repeat outranks everything, and
    so does a REWORDED restatement of the same attribute value, or the same bullet lands twice."""
    nk = _keywords(fact)
    if not nk:
        nf = _normalize(fact).strip()
        return next((e for e in existing if _normalize(e).strip() == nf), None)
    neg = _negated(fact)
    nf = _norm(fact)
    dup = None
    corrected = False
    for e in existing:
        if _norm(e) == nf or _restates(fact, e):
            return e
        if _attribute_match(fact, e) == "correct" or _sv_verb_match(fact, e) == "correct":
            corrected = True
            continue
        if _negated(e) != neg or _distinct(fact, e):
            continue
        ek = _keywords(e)
        if not ek or not _near_duplicate(nk, ek):
            continue
        if _corrects(fact, e):
            corrected = True
        elif dup is None:
            dup = e
    return None if corrected else dup


def _is_duplicate(fact: str, existing: list[str]) -> bool:
    return _duplicate_of(fact, existing) is not None


def duplicate_of(fact: str) -> str | None:
    """What the store already holds that a candidate fact would be discarded as a repeat of (None when
    it would be written). memory_write quotes this: when the de-dup verdict is wrong, the model must at
    least see WHAT is stored instead of telling the user the new value was already known."""
    fact = " ".join((fact or "").split())
    return _duplicate_of(fact, existing_facts()) if fact else None


def corrections(fact: str, existing: list[str]) -> list[str]:
    """Stored facts this one CORRECTS, by any discriminator: close enough that the pair would
    otherwise be a duplicate but with a same-class value swapped inside an identical frame (_corrects),
    the same single-valued attribute about the same subject holding a different value
    (_attribute_match), or the same single-valued verb fact with its place swapped (_sv_verb_match).
    The write path retires these the way it retires superseded refinements — a corrected value
    replaces the stale one instead of coexisting with it."""
    nk = _keywords(fact)
    if not nk:
        return []
    neg = _negated(fact)
    out = []
    for e in existing:
        if _attribute_match(fact, e) == "correct" or _sv_verb_match(fact, e) == "correct":
            out.append(e)
        elif _negated(e) == neg and (ek := _keywords(e)) and _near_duplicate(nk, ek) and _corrects(fact, e):
            out.append(e)
    return out


def attribute_conflicts(fact: str, existing: list[str]) -> list[str]:
    """Stored facts that state the SAME single-valued attribute with a different value but must be kept
    (compound, or naming someone this fact does not). They survive on purpose; the caller reports them so
    the contradiction is spoken rather than silently recalled next session."""
    return [e for e in existing
            if _attribute_match(fact, e) == "blocked" or _sv_verb_match(fact, e) == "blocked"]


def supersedes(fact: str, existing: list[str]) -> list[str]:
    """Which stored facts a new one strictly refines (its keywords are a proper subset of the new one's),
    ABOUT THE SAME SUBJECT. Used by the write path to retire the vaguer entry instead of keeping both.

    A superset is not automatically a refinement: "sister lives in Madrid" contains "lives in Madrid"
    and retired the user's own address as though it had been the vague draft of it."""
    nk = _keywords(fact)
    if not nk:
        return []
    out = []
    for e in existing:
        ek = _keywords(e)
        if ek and ek < nk and not _distinct(fact, e):
            out.append(e)
    return out


def _atomic_write(path: Path, text: str) -> None:
    atomic_file.write_text(path, text)


@contextmanager
def _guard():
    """The RLock alone is per-process. USER.md is the index loaded into EVERY prompt, and a second
    process interleaving here raised a raw FileNotFoundError at the user for a fact that HAD been
    saved — she reported a failure for work she had done."""
    with _lock, atomic_file.exclusive(user_md_path()):
        yield


def topic_facts(topic: str) -> list[str]:
    p = topic_path(topic)
    return _bullets(p.read_text(errors="replace", encoding="utf-8")) if p.exists() else []


def list_topics() -> list[tuple[str, int]]:
    """(slug, fact_count) for every topic file, sorted by count desc then name."""
    d = topics_dir()
    out: list[tuple[str, int]] = []
    if d.exists():
        for f in sorted(d.glob("*.md")):
            out.append((f.stem, len(_bullets(f.read_text(errors="replace", encoding="utf-8")))))
    return sorted(out, key=lambda t: (-t[1], t[0]))


def _read_recent() -> list[str]:
    """Parse the '## Recent' bullets out of the current USER.md (order preserved)."""
    if not user_md_path().exists():
        return []
    text = user_md_path().read_text(errors="replace", encoding="utf-8")
    m = re.search(r"^##\s+Recent\s*$(.*?)(^##\s|\Z)", text, re.M | re.S)
    return _bullets(m.group(1)) if m else []


def _rebuild_index(recent: list[str]) -> None:
    recent = recent[-RECENT_CAP:]
    lines = [_INDEX_HEADER, "## Recent\n"]
    lines.append("\n".join(f"- {f}" for f in recent) if recent else "(nothing yet)")
    lines.append("\n\n## Topics\n")
    topics = list_topics()
    if topics:
        lines.append("\n".join(f"- {slug} → topics/{slug}.md ({n} facts)" for slug, n in topics))
    else:
        lines.append("(no topics yet)")
    _atomic_write(user_md_path(), "\n".join(lines).rstrip() + "\n")


def write_fact(fact: str, topic: str = _DEFAULT_TOPIC, *, filter_ephemeral: bool = False) -> dict:
    """append_fact with its reasoning visible: {written, fact, reason, retired, conflicts, duplicate_of}.

    `retired` is what this write REPLACED, `conflicts` what still contradicts it and could not be —
    the caller needs both, or she says "I don't use the old one any more" about a fact still in the
    store and still read back. A RESTATEMENT writes nothing but still retires what it contradicts —
    refusing that is how five colours survived two corrections; refinements are NOT retired there.

    A fact is ONE line: multi-line text opens a stray '#' section in the system prompt and truncates
    the Recent scan, permanently dropping every later index entry. De-dup runs ACROSS ALL topics
    because the model invents synonym topics, and a per-topic check let one fact pile up under each."""
    fact = (fact or "").strip()
    out = {"written": False, "fact": fact, "reason": "empty", "retired": [], "conflicts": [],
           "duplicate_of": None}
    if not fact:
        return out
    fact = " ".join(fact.split())
    if len(fact) > _MAX_FACT_CHARS:
        fact = fact[:_MAX_FACT_CHARS].rsplit(" ", 1)[0] + "…"
    out["fact"] = fact
    if filter_ephemeral and _looks_ephemeral(fact):
        out["reason"] = "ephemeral"
        return out
    with _guard():
        slug = slugify(topic)
        tp = topic_path(slug)
        all_facts = existing_facts()
        dup = _duplicate_of(fact, all_facts)
        if dup is not None:
            out.update(reason="duplicate", duplicate_of=dup)
            if _restates(fact, dup):
                stale = corrections(fact, all_facts)
                _retire_facts(stale)
                out.update(reason="restated", retired=stale,
                           conflicts=attribute_conflicts(fact, all_facts))
                if stale:
                    _rebuild_index([f for f in _read_recent() if f not in stale])
            return out
        header = f"# {slug}\n\nFacts Kotoba remembers about you in this area.\n\n"
        body = tp.read_text(errors="replace", encoding="utf-8") if tp.exists() else header
        if not body.endswith("\n"):
            body += "\n"
        _atomic_write(tp, body + f"- {fact}\n")
        stale, seen = [], set()
        for f in supersedes(fact, all_facts) + corrections(fact, all_facts):
            if _norm(f) not in seen:
                seen.add(_norm(f))
                stale.append(f)
        if stale:
            _retire_facts(stale)
        recent = [f for f in _read_recent() if _norm(f) != _norm(fact) and f not in stale]
        recent.append(fact)
        _rebuild_index(recent)
        out.update(written=True, reason="written", retired=stale,
                   conflicts=attribute_conflicts(fact, all_facts))
        return out


def append_fact(fact: str, topic: str = _DEFAULT_TOPIC, *, filter_ephemeral: bool = False) -> bool:
    """Save a durable fact into its topic file + the Recent index. De-duped across all topics.
    Returns True if written, False if empty/duplicate/filtered.

    `filter_ephemeral` is for the AUTOMATIC extractor, which turns a whole conversation into candidate
    facts and so needs task narration screened out. It defaults OFF because when the model calls
    memory_write it has already decided the fact is durable — and a caller cannot distinguish a filtered
    fact from a duplicate, so filtering there made the tool answer "Already remembered" for something it
    had just thrown away. Nothing retries after that."""
    return write_fact(fact, topic, filter_ephemeral=filter_ephemeral)["written"]


def _retire_facts(facts: list[str]) -> None:
    """Drop these exact bullets from every topic file. Called under _lock."""
    targets = {_norm(f) for f in facts}
    for tp in sorted(memory_dir().glob("topics/*.md")):
        try:
            lines = tp.read_text(errors="replace", encoding="utf-8").splitlines(keepends=True)
        except OSError:
            continue
        kept = [ln for ln in lines
                if not (ln.lstrip().startswith("- ") and _norm(ln.lstrip()[2:]) in targets)]
        if len(kept) != len(lines):
            _atomic_write(tp, "".join(kept))


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def read_user_md() -> str:
    return user_md_path().read_text(errors="replace", encoding="utf-8") if user_md_path().exists() else ""


def existing_facts() -> list[str]:
    """Every fact across all topics. Not what the prompt sees."""
    facts: list[str] = []
    for slug, _ in list_topics():
        facts.extend(topic_facts(slug))
    return facts


def recent_facts() -> list[str]:
    """The '## Recent' facts (newest last) — includes anything memory_write saved this turn, so the
    proactive extractor can be told what is ALREADY stored (its cross-language de-dup context)."""
    return _read_recent()


def topic_summary(limit: int = _MAX_TOPICS_IN_SUMMARY, counts: bool = True) -> str:
    """The biggest topics, capped, with a tail saying how many were left out. One string for both
    readers of it — the prompt's pointer line and the extractor's reuse list — so the names she is shown
    inline are the names the extractor is told to file under.

    `counts` off for the extractor: every topic that survives the cap is already one of the biggest, so
    the sizes discriminate nothing there and cost 68 of the 193 characters. The tail is what carries the
    weight — "+67 more topics" is what says the store already has plenty to file under."""
    topics = list_topics()
    if not topics:
        return ""
    shown = topics[:limit]
    summary = ", ".join(f"{slug} ({n})" if counts else slug for slug, n in shown)
    if len(topics) > len(shown):
        summary += f", +{len(topics) - len(shown)} more topics"
    return summary


def _topic_of() -> dict[str, str]:
    """Which topic file each fact is filed under, keyed by the normalized fact. First writer wins, the
    same order `existing_facts` reads in."""
    where: dict[str, str] = {}
    for slug, _n in list_topics():
        for fact in topic_facts(slug):
            where.setdefault(_norm(fact), slug)
    return where


def facts_for_prompt() -> list[str]:
    """What goes inline in the system prompt: the Recent facts, each TAGGED with the topic file it
    lives in, plus one line pointing at the topic files so she knows what else she has.

    The tag is what makes the two halves relate: without it the prompt showed a list of facts and,
    underneath, a list of topic names, with nothing saying which fact came from which drawer. It costs
    ~1 slug per line. A Recent entry retired since the last index rebuild has no topic and is left
    bare rather than mislabelled. The pointer line is bounded and says how many facts it stands for —
    unbounded it grew past a thousand characters across 54 free-form topics, and with no total it read
    as "this is everything", which is the opposite of true."""
    recent = _read_recent()
    topics = list_topics()
    where = _topic_of()
    out = [f"{f} [{where[_norm(f)]}]" if _norm(f) in where else f for f in recent]
    if topics:
        total = sum(n for _s, n in topics)
        out.append(
            f"(This list is the {len(recent)} most recent of {total} facts, across {len(topics)} topics: "
            f"{topic_summary()}. Use memory_recall for anything not shown — it is saved, just not inline.)"
        )
    return out


def _stem(word: str) -> str:
    """One inflected word reduced to the form its siblings share. SEARCH ONLY — never the write side.

    Facts are stored in the third person ("Lives in Madrid") and asked in the first ("where do I
    live"), so the two never shared a token: on the live store, 97 of 168 facts open with `wants` and
    "what do I want" matched 10 of them; stemming both sides symmetrically took that to 103. Suffixes
    first, then a trailing a/e/o, which is what carries Spanish conjugation (vivo/vive → viv).

    Kept out of `_keywords` on purpose: that feeds the duplicate and RETIREMENT machinery, and a wider
    notion of sameness there deletes facts nobody contradicted. Widening SEARCH costs a longer
    candidate list and nothing else — 749 words collapse to 655 stems, one bad family (data/date)."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) - len(suffix) >= 3 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    return word[:-1] if len(word) >= 4 and word[-1] in "aeo" else word


def _search_keys(text: str) -> frozenset[str]:
    return frozenset(_stem(w) for w in _keywords(text))


def search_facts(query: str, limit: int = 12) -> list[dict]:
    """Keyword search across ALL topics (accent/stopword-normalized, then stemmed — see `_stem`). Returns
    [{topic, fact}] ranked by how much of the query's keywords a fact covers. This is what makes recall
    robust when the model guesses the 'wrong' topic name — a fact about Facebook is found whether it's
    filed under social-media, search, or task. Empty when nothing matches."""
    qk = _search_keys(query)
    if not qk:
        return []
    hits: list[tuple[float, int, str, str]] = []
    for slug, _ in list_topics():
        for fact in topic_facts(slug):
            fk = _search_keys(fact)
            inter = len(qk & fk)
            if inter:
                hits.append((inter / len(qk), inter, slug, fact))
    hits.sort(key=lambda h: (-h[0], -h[1]))
    return [{"topic": slug, "fact": fact} for _s, _i, slug, fact in hits[:limit]]


def recall(topic: str) -> dict:
    """A topic's own facts + `related` — what the same word finds filed under OTHER topics. With an
    empty topic file the search result IS the answer; with neither, we list the topics we do have.

    A topic hit used to short-circuit, which made the topic NAME the whole answer: on the live store
    57 of 79 topics held facts a search on the same word finds under a different slug, so "where do I
    live" was decided by which name she guessed — `personal` returned a stale Barcelona, `location`
    returned Madrid, and neither branch could see the other. `facts` still means exactly what this
    topic file holds, so `path` stays honest; cross-store hits are a separate list carrying their own
    topic and cannot drown it. The cap is where the measured hit-rate stops moving: 4/8 → 8/8 at four."""
    slug = slugify(topic)
    facts = topic_facts(slug)
    if facts:
        seen = {_norm(f) for f in facts}
        related = [h for h in search_facts(topic, _RELATED_CAP + len(facts))
                   if _norm(h["fact"]) not in seen][:_RELATED_CAP]
        return {"topic": slug, "path": f"topics/{slug}.md", "facts": facts, "related": related}
    hits = search_facts(topic)
    if hits:
        return {"topic": slug, "path": None, "facts": [h["fact"] for h in hits], "searched": True}
    return {"topic": slug, "path": None, "facts": [], "available_topics": [s for s, _ in list_topics()]}


# A lead alone never discards a fact — see the module docstring.
_EPHEMERAL_LEADS = (
    "creating ", "adding ", "moving ", "deleting ", "removing ", "editing ", "running ",
    "renaming ", "is trying to", "trying to ",
)
_TASK_MARKERS = (" command", " folder", " directory", " file", " script")
_FILE_TOKEN = re.compile(
    r"\b[\w-]{1,60}\.(?:txt|html?|css|jsx?|tsx?|py|json|ya?ml|md|csv|log|sh)\b", re.I
)
# Unambiguous noise: no English or Spanish sentence about a person contains these.
_EPHEMERAL_SUBSTRINGS = (
    "note for today", "today's note", "site search task", "titled 'prueba", "titled prueba",
)


def _looks_ephemeral(fact: str) -> bool:
    """True for a clearly transient task-action (not a durable fact).

    Deliberately biased toward KEEPING. A wrongly kept transient fact is visible clutter `dedupe_store`
    could sweep if anything called it; a wrongly dropped durable fact is invisible and self-sealing, because the
    caller has already told the model it was remembered. Matching bare substrings did the latter at
    scale: `.js` fired on "Next.js" and "Node.js", `" command"` on "army command structure", and the
    gerund list ate every life event phrased as one."""
    f = _normalize(fact).strip()
    if any(s in f for s in _EPHEMERAL_SUBSTRINGS):
        return True
    if f.startswith(_EPHEMERAL_LEADS):
        return bool(_FILE_TOKEN.search(f)) or any(m in f for m in _TASK_MARKERS)
    return False


def dedupe_store(drop_ephemeral: bool = False) -> dict:
    """Maintenance: collapse duplicate facts across ALL topics using the current cross-topic, fuzzy,
    accent-normalized de-dup. Keeps the FIRST occurrence (topics sorted by name) and drops later repeats;
    emptied topic files are removed and the index rebuilt. Naturally consolidates synonym topics
    (proyecto/proyectos, search/search-activity/…). When drop_ephemeral=True, also removes clearly transient
    task-actions (_looks_ephemeral). Returns {before, after, removed, removed_facts}."""
    with _guard():
        pairs: list[tuple[str, str]] = []
        for slug, _ in sorted(list_topics()):
            for fact in topic_facts(slug):
                pairs.append((slug, fact))
        before = len(pairs)
        kept_by_topic: dict[str, list[str]] = {}
        kept_all: list[str] = []
        removed_facts: list[str] = []
        for slug, fact in pairs:
            if drop_ephemeral and _looks_ephemeral(fact):
                removed_facts.append(fact)
                continue
            if _is_duplicate(fact, kept_all):
                removed_facts.append(fact)
                continue
            kept_by_topic.setdefault(slug, []).append(fact)
            kept_all.append(fact)
        for slug, _ in list(list_topics()):
            p = topic_path(slug)
            facts = kept_by_topic.get(slug)
            if facts:
                header = f"# {slug}\n\nFacts Kotoba remembers about you in this area.\n\n"
                _atomic_write(p, header + "\n".join(f"- {f}" for f in facts) + "\n")
            elif p.exists():
                p.unlink()
        _rebuild_index(kept_all)
        return {"before": before, "after": len(kept_all), "removed": before - len(kept_all),
                "removed_facts": removed_facts}


def attribute_conflict_report() -> list[dict]:
    """READ-ONLY audit: every group of stored facts that state the same single-valued attribute about the
    same subject with different values — the pile-up this store already had before the rule existed.
    Returns [{attribute, topics, facts}] and changes NOTHING.

    Deliberately not a sweeper. Which of six favourite colours is the true one is the USER's answer, not
    a de-dup verdict, and store order is not chronological (topics are listed by size), so "newest" here
    would be a guess. Saying the current value once through the write path retires every stale sibling
    the guards allow; whatever this report still lists after that is a decision for a human."""
    groups: dict[tuple, dict] = {}
    for slug, _ in list_topics():
        for fact in topic_facts(slug):
            parts = _attribute_parts(fact)
            if not parts:
                continue
            attr, scope, value = parts
            key = (attr, scope, _negated(fact))
            g = groups.setdefault(key, {"attribute": attr, "topics": [], "facts": [], "_values": set()})
            g["topics"].append(slug)
            g["facts"].append(fact)
            g["_values"].add(value)
    out = []
    for g in groups.values():
        if len(g.pop("_values")) > 1:
            out.append(g)
    return out


def delete_topic(topic: str) -> bool:
    """Delete a topic file and rebuild the index. Returns True if it existed."""
    with _guard():
        p = topic_path(topic)
        if not p.exists():
            return False
        p.unlink()
        live = set()
        for slug, _ in list_topics():
            live.update(_norm(f) for f in topic_facts(slug))
        recent = [f for f in _read_recent() if _norm(f) in live]
        _rebuild_index(recent)
        return True


def migrate_from_db_facts(db_facts: list[str]) -> None:
    """One-time: build the structured store from any pre-existing data (old flat USER.md + DB facts),
    filing everything under the 'general' topic. No-op once topic files exist (zero loss, idempotent)."""
    with _guard():
        if topics_dir().exists() and any(topics_dir().glob("*.md")):
            return
        old_flat: list[str] = []
        if user_md_path().exists():
            text = user_md_path().read_text(errors="replace", encoding="utf-8")
            if "## Topics" not in text:  # old flat format, not the new index
                old_flat = _bullets(text)
        seen: set[str] = set()
        for fact in [*old_flat, *(db_facts or [])]:
            f = (fact or "").strip()
            if f and _norm(f) not in seen:
                seen.add(_norm(f))
                append_fact(f, _DEFAULT_TOPIC)
        if not seen and not user_md_path().exists():
            _rebuild_index([])
