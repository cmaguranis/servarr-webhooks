_LANG_MAP = {
    "english": "eng",
    "japanese": "jpn",
    "french": "fra",
    "spanish": "spa",
    "german": "deu",
    "korean": "kor",
    "chinese": "zho",
    "portuguese": "por",
    "italian": "ita",
}


def parse_lang(name: str | None) -> str | None:
    return _LANG_MAP.get((name or "").lower().strip()) if name else None
