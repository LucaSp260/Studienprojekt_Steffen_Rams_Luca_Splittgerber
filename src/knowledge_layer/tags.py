"""Local tag normalization for newly extracted notes; no model calls."""
import re
import unicodedata

MAX_TAGS = 5

def normalize_tag(value):
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    value = re.sub(r"[\W_]+", "-", value).strip("-_")
    # Count hyphenated terms as words; never retain sentence-length labels.
    words = [word for word in value.split("-") if word]
    if not words or len(words) > 3 or len(value) > 50:
        return None
    return "-".join(words)

def tag_key(value):
    # Space, underscore, hyphen and camel/case variations share one identity.
    return re.sub(r"[^\w]+|_", "", unicodedata.normalize("NFKC", value).casefold())

def normalize_tags(values, existing=()):
    canonical = {}
    for value in existing:
        short = normalize_tag(value)
        if short:
            canonical.setdefault(tag_key(value), set()).add(short)
    output = []
    for value in values:
        short = normalize_tag(value)
        if not short:
            continue
        candidates = canonical.get(tag_key(short), set())
        if len(candidates) == 1:
            short = next(iter(candidates))
        if short not in output:
            output.append(short)
        if len(output) == MAX_TAGS:
            break
    return output
