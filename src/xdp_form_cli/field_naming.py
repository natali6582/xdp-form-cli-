"""Rule-based field naming for auto-detected fields.

No LLM and no ML: names come from a predefined Hebrew->English dictionary,
a deterministic Hebrew transliteration table, and simple string cleaning.
"""

from __future__ import annotations

import re

# Logical field type -> name prefix. Signatures use ``img`` per the project
# rule that signature placeholders are image fields, never /Sig fields.
TYPE_PREFIXES = {
    "text": "txt",
    "textarea": "txt",
    "checkbox": "chk",
    "signature": "img",
    "image": "img",
    "date": "dt",
    "dropdown": "dd",
}

GENERIC_BASE = "Field"

# Predefined Hebrew label -> English name dictionary (extensible per call).
DEFAULT_LABEL_DICTIONARY = {
    "שם": "Name",
    "שם מלא": "FullName",
    "שם פרטי": "FirstName",
    "שם משפחה": "LastName",
    "תאריך": "Date",
    "תאריך לידה": "BirthDate",
    "חתימה": "Signature",
    "כתובת": "Address",
    "טלפון": "Phone",
    "נייד": "Mobile",
    'דוא"ל': "Email",
    "דואל": "Email",
    "מייל": "Email",
    "ת.ז.": "IdNumber",
    "תז": "IdNumber",
    "תעודת זהות": "IdNumber",
    "מספר חשבון": "AccountNumber",
    "סכום": "Amount",
    "הערות": "Notes",
}

# Deterministic Hebrew letter transliteration (final forms included).
HEBREW_TRANSLITERATION = {
    "א": "a", "ב": "b", "ג": "g", "ד": "d", "ה": "h", "ו": "v", "ז": "z",
    "ח": "ch", "ט": "t", "י": "y", "כ": "k", "ך": "k", "ל": "l", "מ": "m",
    "ם": "m", "נ": "n", "ן": "n", "ס": "s", "ע": "a", "פ": "p", "ף": "p",
    "צ": "ts", "ץ": "ts", "ק": "k", "ר": "r", "ש": "sh", "ת": "t",
}

_MAX_BASE_LENGTH = 32


def generate_field_name(
    label: str,
    field_type: str,
    used_names: dict[str, int],
    *,
    dictionary: dict[str, str] | None = None,
) -> str:
    """Generate a unique field name from a nearby label, without any LLM.

    Steps: clean the label, look it up in the dictionary, transliterate
    Hebrew if unmatched, CamelCase, apply the type prefix, then suffix
    ``_1``/``_2``... on duplicates.
    """
    prefix = TYPE_PREFIXES.get(field_type, "txt")
    base = _base_from_label(label, dictionary)
    name = f"{prefix}{base}"
    count = used_names.get(name, 0)
    used_names[name] = count + 1
    return name if count == 0 else f"{name}_{count}"


def _base_from_label(label: str, dictionary: dict[str, str] | None) -> str:
    cleaned = _clean_label(label)
    if not cleaned:
        return GENERIC_BASE

    merged = dict(DEFAULT_LABEL_DICTIONARY)
    if dictionary:
        merged.update(dictionary)
    if cleaned in merged:
        return merged[cleaned]

    words = cleaned.split()
    parts: list[str] = []
    for word in words:
        if _has_hebrew(word):
            word = _transliterate(word)
        word = re.sub(r"[^A-Za-z0-9]+", "", word)
        if word:
            parts.append(word[0].upper() + word[1:])
    base = "".join(parts)[:_MAX_BASE_LENGTH]
    if not base or not any(ch.isalpha() for ch in base):
        return GENERIC_BASE
    return base


def _clean_label(label: str) -> str:
    # Keep letters (any script), digits, quotes/dots used inside Hebrew
    # abbreviations such as ת.ז. and דוא"ל, and spaces.
    text = re.sub(r"[^\w\s\.\"]+", " ", label, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(". ")


def _has_hebrew(text: str) -> bool:
    return any("֐" <= ch <= "׿" for ch in text)


def _transliterate(word: str) -> str:
    return "".join(HEBREW_TRANSLITERATION.get(ch, ch) for ch in word)
