"""
parser.py — Nutrition table parser for PaddleOCR output.

PaddleOCR splits multi-column tables into separate lines:
    energy (kcal #)   ← nutrient name
    386.0             ← col 1 (per 100g)
    135.1             ← col 2 (per serving)  ← we want this
    6.8%              ← col 3 (RDA %)

Strategy: detect nutrient name, collect next/prev value lines,
pick the per-serving column (smaller non-RDA value).
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ─── OCR Cleanup ──────────────────────────────────────────────────────────────

def clean_ocr_text(text: str) -> str:
    text = re.sub(r'\(8\)|\(Q\)', '(g)', text)
    text = re.sub(r'kcall|keal|Kcal', 'kcal', text)
    text = re.sub(r'\.{3,}', ' ', text)
    return text


# ─── Sanity Bounds ────────────────────────────────────────────────────────────
# Rejects OCR garbage like trans_fat=1388 (amino acid value bled in)

NUTRIENT_BOUNDS = {
    "energy_kcal":      (0,   900),
    "protein_g":        (0,   100),
    "carbohydrates_g":  (0,   100),
    "sugar_g":          (0,   100),
    "added_sugar_g":    (0,   100),
    "total_fat_g":      (0,    80),
    "saturated_fat_g":  (0,    50),
    "trans_fat_g":      (0,    10),
    "dietary_fiber_g":  (0,    50),
    "sodium_mg":        (0,  3000),
    "cholesterol_mg":   (0,   500),
    "calcium_mg":       (0,  2000),
    "iron_mg":          (0,   100),
    "potassium_mg":     (0,  5000),
}

def in_bounds(nutrient: str, value: float) -> bool:
    if nutrient not in NUTRIENT_BOUNDS:
        return True
    lo, hi = NUTRIENT_BOUNDS[nutrient]
    return lo <= value <= hi


# ─── Core nutrients (vs amino acids / vitamins) ───────────────────────────────

CORE_NUTRIENTS = {
    "energy_kcal", "protein_g", "carbohydrates_g", "sugar_g", "added_sugar_g",
    "total_fat_g", "saturated_fat_g", "trans_fat_g", "dietary_fiber_g",
    "sodium_mg", "cholesterol_mg",
}


# ─── Nutrient Name Registry ───────────────────────────────────────────────────

NUTRIENT_ALIASES = {
    "energy_kcal": [
        "energy", "energy (kcal)", "energy (kcal #)", "calories", "calorie",
        "energy kcal", "energy(kcal)", "cal",
        "ऊर्जा", "ஆற்றல்",
    ],
    "protein_g": [
        "protein", "protein (g)", "total protein", "crude protein",
        "orotein",  # common PaddleOCR misread
        "प्रोटीन", "புரதம்",
    ],
    "carbohydrates_g": [
        "carbohydrate", "carbohydrates", "total carbohydrate",
        "total carbohydrates", "carbs", "carbohydrate (g)",
        "कार्बोहाइड्रेट", "कार्बोहाइड्रेट्स", "கார்போஹைட்ரேட்",
    ],
    "sugar_g": [
        "sugars", "sugar", "total sugars", "total sugar",
        "of which sugars", "-total sugars", "- total sugars",
        "total sugars^", "cotal sugars", "cotal sugars (g)",
        "चीनी", "शर्करा", "कुल शर्करा", "சர்க்கரை", "மொத்த சர்க்கரை",
    ],
    "added_sugar_g": [
        "added sugars", "added sugar", "-added sugars", "- added sugars",
        "added sugars^",
        "अतिरिक्त शर्करा", "जोड़ी गई चीनी", "சேர்க்கப்பட்ட சர்க்கரை",
    ],
    "total_fat_g": [
        "fat", "total fat", "total fat (g)", "fat (g)",
        "otal fat",  # PaddleOCR drops leading t
        "वसा", "कुल वसा", "மொத்த கொழுப்பு",
    ],
    "saturated_fat_g": [
        "saturated fat", "saturated", "sat fat",
        "-saturated fat", "- saturated fat",
        "saturated fat (g)", "saturated fatty acid",
        "saturated fatty acids",
        "संतृप्त वसा", "செறிவூட்டப்பட்ட கொழுப்பு",
    ],
    "trans_fat_g": [
        "trans fat", "trans fatty acid", "trans fat (g)",
        "- trans fat", "-trans fat", "trans fatty acids",
        "ट्रांस वसा", "டிரான்ஸ் கொழுப்பு",
    ],
    "dietary_fiber_g": [
        "dietary fibre", "dietary fiber", "fibre", "fiber",
        "dietary fibre (g)", "total fibre",
        "आहार रेशा", "फाइबर", "நார்ச்சத்து", "உணவுநார்",
    ],
    "sodium_mg": ["sodium", "sodium (mg)", "sodium*", "सोडियम", "சோடியம்"],
    "cholesterol_mg": ["cholesterol", "cholesterol (mg)", "cholesterol^", "कोलेस्ट्रॉल", "கொலஸ்ட்ரால்"],
    "calcium_mg": ["calcium", "calcium (mg)", "कैल्शियम", "கால்சியம்"],
    "iron_mg": ["iron", "iron (mg)", "लौह", "இரும்பு"],
    "potassium_mg": ["potassium", "potassium (mg)", "पोटैशियम", "பொட்டாசியம்"],
}

_ALIAS_MAP: dict[str, str] = {}
for _canon, _aliases in NUTRIENT_ALIASES.items():
    for _alias in dict.fromkeys(_aliases):
        _ALIAS_MAP[_alias.lower().strip()] = _canon


def match_nutrient(line: str) -> Optional[str]:
    cleaned = re.sub(r'[\*\^†#•]+$', '', line.lower().strip())
    cleaned = re.sub(r'\s*(g|mg|kcal|kj|%)\s*$', '', cleaned).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    if cleaned in _ALIAS_MAP:
        return _ALIAS_MAP[cleaned]
    for alias, canon in _ALIAS_MAP.items():
        if cleaned.startswith(alias) and len(alias) >= 4:
            return canon
    return None


# ─── Value Helpers ────────────────────────────────────────────────────────────

_NUM_RE     = re.compile(r'<?(\d+\.?\d*)')
_PERCENT_RE = re.compile(r'^\d+\.?\d*\s*%')
_RDA_RE     = re.compile(r'%\s*rda|%rda|\brda\b', re.I)


def is_value_line(line: str) -> bool:
    # Normalise I→1 in numeric context before checking
    stripped = re.sub(r'(?<=[0-9.])I|^I(?=[0-9.])', '1', line.strip())
    if not stripped:
        return False
    if re.match(r'^<?(\d+\.?\d*)\s*(g|mg|kcal|kj|ml|mcg|%)?$', stripped, re.I):
        return True
    if re.match(r'^\d+\.?\d*\s*(?:g|mg|kcal|kj)\s*$', stripped, re.I):
        return True
    return False


def is_rda_line(line: str) -> bool:
    stripped = line.strip()
    if _PERCENT_RE.match(stripped):
        return True
    if _RDA_RE.search(stripped):
        return True
    return False


def extract_number(text: str) -> Optional[float]:
    text = text.strip()
    if re.match(r'^[-–—]$', text):
        return 0.0
    if re.match(r'^(nil|nd|n\.d\.|trace)$', text, re.I):
        return 0.0
    # Normalise I→1
    text = re.sub(r'(?<=[0-9.])I|^I(?=[0-9.])', '1', text)
    m = _NUM_RE.search(text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def pick_serving_value(values: list[float]) -> Optional[float]:
    """Pick per-serving value from a list (usually smaller than per-100g)."""
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    v1, v2 = values[0], values[1]
    if v1 == 0 and v2 == 0:
        return 0.0
    if v1 > 0 and v2 > 0:
        ratio = max(v1, v2) / min(v1, v2)
        if ratio < 1.3:
            return v1
        return min(v1, v2)
    return v1 if v1 > 0 else v2


# ─── Inline Regex Fallback ────────────────────────────────────────────────────

def parse_nutrition_inline(ocr_text: str) -> dict[str, float]:
    """Single-line format fallback: 'Protein 24g 79g 44%' → picks first value."""
    text   = clean_ocr_text(ocr_text)
    result = {}
    PATTERNS = [
        ("energy_kcal",     r"energy[\s\(kcal#\)]*[\s:]+(\d+\.?\d*)\s*kcal"),
        ("protein_g",       r"protein[\s\(g\)]*[\s:]+(\d+\.?\d*)\s*g"),
        ("carbohydrates_g", r"carbohydrate[s]?[\s\(g\)]*[\s:]+(\d+\.?\d*)\s*g"),
        ("sugar_g",         r"(?:total\s+)?sugar[s]?[\s:]+(\d+\.?\d*)\s*g"),
        ("total_fat_g",     r"(?:total\s+)?fat[\s\(g\)]*[\s:]+(\d+\.?\d*)\s*g"),
        ("saturated_fat_g", r"saturated[\s\(g\)]*[\s:]+(\d+\.?\d*)\s*g"),
        ("dietary_fiber_g", r"(?:dietary\s+)?fi[b]?[e]?r[\s\(g\)]*[\s:]+(\d+\.?\d*)\s*g"),
        ("sodium_mg",       r"sodium[\s\(mg\)]*[\s:]+(\d+\.?\d*)\s*mg"),
        ("cholesterol_mg",  r"cholesterol[\s\(mg\)]*[\s:]+(\d+\.?\d*)\s*mg"),
    ]
    for key, pattern in PATTERNS:
        if key in result:
            continue
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                v = float(m.group(1))
                if in_bounds(key, v):
                    result[key] = v
            except ValueError:
                pass
    return result


# ─── Main Parser ──────────────────────────────────────────────────────────────

def parse_nutrition_rows(ocr_text: str) -> dict[str, float]:
    """
    Look-ahead/look-behind parser for PaddleOCR column-split output.
    Handles interleaved amino acid columns by skipping non-core nutrients.
    """
    text  = clean_ocr_text(ocr_text)
    lines = [l.strip() for l in text.split('\n')]
    result = {}
    i = 0

    while i < len(lines):
        line = lines[i]
        if not line:
            i += 1
            continue

        nutrient = match_nutrient(line)

        if nutrient and nutrient not in result:
            # Pass A: look forward for values
            values = []
            j = i + 1
            while j < len(lines) and len(values) < 4:
                nxt = lines[j].strip()
                if not nxt:
                    j += 1; continue
                if match_nutrient(nxt):
                    break
                if is_rda_line(nxt):
                    j += 1; continue
                if is_value_line(nxt):
                    v = extract_number(nxt)
                    if v is not None:
                        values.append(v)
                else:
                    break
                j += 1

            # Pass B: look backward if nothing found forward
            if not values:
                k = i - 1
                backward = []
                while k >= 0 and len(backward) < 4:
                    prev = lines[k].strip()
                    if not prev:
                        k -= 1; continue
                    prev_nutrient = match_nutrient(prev)
                    if prev_nutrient and prev_nutrient in CORE_NUTRIENTS:
                        break
                    if prev_nutrient:
                        k -= 1; continue  # skip amino acid lines
                    if is_rda_line(prev):
                        k -= 1; continue
                    if is_value_line(prev):
                        v = extract_number(prev)
                        if v is not None:
                            backward.insert(0, v)
                    else:
                        break
                    k -= 1
                values = backward

            chosen = pick_serving_value(values)
            if chosen is not None and in_bounds(nutrient, chosen):
                result[nutrient] = chosen
                logger.debug(f"[parser] {nutrient} = {chosen} (values={values})")

        i += 1

    # Secondary pass: inline regex fills gaps + corrects out-of-bounds values
    inline = parse_nutrition_inline(ocr_text)
    for k, v in inline.items():
        if k not in result and in_bounds(k, v):
            result[k] = v
        elif k in result and not in_bounds(k, result[k]) and in_bounds(k, v):
            result[k] = v  # replace bad value with inline value

    # Final bounds sweep — remove anything still out of range
    for k in list(result.keys()):
        if not in_bounds(k, result[k]):
            logger.warning(f"[parser] dropping {k}={result[k]} (out of bounds)")
            del result[k]

    return result


# ─── Serving Size ─────────────────────────────────────────────────────────────

SERVING_PATTERNS = [
    re.compile(r'serving\s+size[^:]*:\s*.*?(\d+\.?\d*)\s*g', re.I),
    re.compile(r'serving\s+size.*?(\d+\.?\d*)\s*g', re.I),
    re.compile(r'(\d+\.?\d*)\s*g\s+(?:per\s+serving|per\s+scoop)', re.I),
    re.compile(r'(?:1\s+)?scoop[^(]*\((\d+\.?\d*)\s*g', re.I),
    re.compile(r'per\s+(\d+\.?\d*)\s*g\s+serving', re.I),
    re.compile(r'(\d+\.?\d*)\s*g\s*\(about\s+1\s+scoop\)', re.I),
]

def parse_serving_size(ocr_text: str) -> Optional[float]:
    text = clean_ocr_text(ocr_text)
    for pattern in SERVING_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                val = float(m.group(1))
                if 5 <= val <= 500:
                    return val
            except ValueError:
                pass
    return None


# ─── Ingredients ─────────────────────────────────────────────────────────────

def parse_ingredients(ocr_text: str) -> Optional[str]:
    text = clean_ocr_text(ocr_text)
    m = re.search(
        r'ingredients?\s*[:\-]\s*(.{20,1000}?)(?:\n\n|\Z|allergen|contains|warning|fssai|nutrition)',
        text, re.IGNORECASE | re.DOTALL
    )
    if m:
        ing = m.group(1).replace('\n', ' ').strip()
        ing = re.sub(r'\s{2,}', ' ', ing)
        return ing[:600] if len(ing) > 600 else ing
    return None


# ─── FSSAI ────────────────────────────────────────────────────────────────────

FSSAI_PATTERN  = re.compile(r'\b(\d{14})\b')
VALID_STATE_CODES = {str(i) for i in range(10, 36)}

def parse_fssai_number(ocr_text: str) -> Optional[str]:
    text = ocr_text.replace('O', '0').replace('l', '1').replace('I', '1')
    for m in FSSAI_PATTERN.finditer(text):
        if m.group(1)[:2] in VALID_STATE_CODES:
            return m.group(1)
    return None


# ─── Confidence ───────────────────────────────────────────────────────────────

def assess_confidence(nutrition: dict, serving_size: Optional[float]) -> str:
    count       = len(nutrition)
    has_protein = "protein_g" in nutrition
    has_energy  = "energy_kcal" in nutrition
    if count >= 4 and has_protein and has_energy:
        return "high"
    if count >= 2 or (has_protein and serving_size):
        return "medium"
    return "low"
