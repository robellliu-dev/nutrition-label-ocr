import re

NUTRIENT_ALIASES = {
    "energy_kcal": ["energy", "ऊर्जा", "ஆற்றல்"],
    "protein_g": ["protein", "orotein", "total protein", "प्रोटीन", "புரதம்"],
    "carbohydrates_g": ["carbohydrate", "कार्बोहाइड्रेट", "कार्बोहाइड्रेट्स", "கார்போஹைட்ரேட்"],
    "sugar_g": [
        "total sugars", "cotal sugars", "- total sugars", "sugars (g)",
        "चीनी", "शर्करा", "कुल शर्करा", "சர்க்கரை", "மொத்த சர்க்கரை",
    ],
    "added_sugar_g": ["added sugars", "- added sugars", "अतिरिक्त शर्करा", "जोड़ी गई चीनी", "சேர்க்கப்பட்ட சர்க்கரை"],
    "dietary_fiber_g": [
        "dietary fibre", "dietary fiber", "- dietary fibre", "- dietary fiber",
        "आहार रेशा", "फाइबर", "நார்ச்சத்து", "உணவுநார்",
    ],
    "total_fat_g": ["total fat", "otal fat", "वसा", "कुल वसा", "மொத்த கொழுப்பு"],
    "saturated_fat_g": ["saturated fat", "saturated fatty", "- saturated", "संतृप्त वसा", "செறிவூட்டப்பட்ட கொழுப்பு"],
    "trans_fat_g": ["trans fat", "trans fats", "- trans", "ट्रांस वसा", "டிரான்ஸ் கொழுப்பு"],
    "sodium_mg": ["sodium", "सोडियम", "சோடியம்"],
    "cholesterol_mg": ["cholesterol", "कोलेस्ट्रॉल", "கொலஸ்ட்ரால்"],
}

_ALIAS_MAP = {}
for _canon, _aliases in NUTRIENT_ALIASES.items():
    for _alias in dict.fromkeys(_aliases):
        _ALIAS_MAP[_alias.lower().strip()] = _canon


def match_nutrient(text):
    t = re.sub(r'[\*\^†#•]+$', '', text.lower().strip())
    t = re.sub(r'\s*(g|mg|kcal|kj|%)\s*$', '', t).strip()
    t = re.sub(r'\s+', ' ', t)
    return _ALIAS_MAP.get(t)


def extract_number(text):
    cleaned = text.replace("I", "1").replace("O", "0").replace(",", ".")
    m = re.search(r"<?\s*(\d+\.?\d*)", cleaned)
    return float(m.group(1)) if m else None


# ─── Per-100g expected ranges ─────────────────────────────────────────────────
# Standard FSSAI comparison unit. All extracted values normalised to this.

PER_100G_RANGES = {
    "energy_kcal":     (50,    900),
    "protein_g":       (1,     100),
    "carbohydrates_g": (0,     100),
    "sugar_g":         (0,     100),
    "added_sugar_g":   (0,     100),
    "total_fat_g":     (0,     100),
    "saturated_fat_g": (0,     50),
    "trans_fat_g":     (0,     10),
    "dietary_fiber_g": (0,     50),
    "sodium_mg":       (0,    3000),
    "cholesterol_mg":  (0,    500),
}

# Per-serving ranges — used to identify and exclude serving-size column
PER_SERVING_RANGES = {
    "energy_kcal":     (20,   600),
    "protein_g":       (1,    60),
    "carbohydrates_g": (0,    60),
    "sugar_g":         (0,    40),
    "added_sugar_g":   (0,    30),
    "total_fat_g":     (0,    30),
    "saturated_fat_g": (0,    15),
    "trans_fat_g":     (0,     5),
    "dietary_fiber_g": (0,    15),
    "sodium_mg":       (0,   800),
    "cholesterol_mg":  (0,   200),
}

def in_per_100g_range(nutrient, value):
    lo, hi = PER_100G_RANGES.get(nutrient, (0, 1e9))
    return lo <= value <= hi

def in_per_serving_range(nutrient, value):
    lo, hi = PER_SERVING_RANGES.get(nutrient, (0, 1e9))
    return lo <= value <= hi


def parse_nutrition_table(blocks):
    """
    Extract per-100g values from nutrition label blocks.
    Labels typically have: | nutrient | per 100g | per serving | %RDA |
    We want the per-100g column — the larger of the two numeric columns.
    """
    if not blocks:
        return {}

    result = {}

    max_x    = max(b["x2"] for b in blocks)
    x_cap    = max_x * 0.85
    y_thresh = max_x * 0.025

    for b in blocks:
        nutrient = match_nutrient(b["text"])
        if not nutrient:
            continue

        candidates = []
        for other in blocks:
            dx = other["cx"] - b["cx"]
            if dx > 0 and abs(other["cy"] - b["cy"]) < y_thresh and dx < x_cap:
                v = extract_number(other["text"])
                if v is not None:
                    candidates.append((other["cx"], v))

        candidates.sort(key=lambda c: c[0])
        values = [v for _, v in candidates[:3]]

        if not values:
            continue

        if len(values) >= 2:
            # Pick the value that best fits per-100g range.
            # When both fit (per-serving and per-100g overlap for some nutrients),
            # take the LARGER one — per-100g is always >= per-serving.
            per_100g_candidates = [v for v in values if in_per_100g_range(nutrient, v)]
            if per_100g_candidates:
                result[nutrient] = max(per_100g_candidates)
            else:
                result[nutrient] = max(values)  # best guess
        else:
            v = values[0]
            if in_per_100g_range(nutrient, v):
                result[nutrient] = v
            # else: single value out of range — skip rather than store wrong data

    return result


def parse_serving_size(blocks):
    for b in blocks:
        text = b["text"].lower()
        if any(k in text for k in ["serving size", "scoop", "per 35", "per 30",
                                    "per 36", "per 45", "serving ="]):
            cleaned = re.sub(r'\bI\b', '1', b["text"])
            m = re.search(r'(\d+\.?\d*)\s*g', cleaned)
            if m:
                val = float(m.group(1))
                if 5 <= val <= 500:
                    return val
    return None


def parse_fssai_from_blocks(blocks):
    for b in blocks:
        text = b["text"].replace("I", "1").replace("O", "0")
        m = re.search(r"\b\d{14}\b", text)
        if m:
            num = m.group(0)
            if 10 <= int(num[:2]) <= 35:
                return num
    return None


def assess_confidence(nutrition, serving_size):
    count    = len(nutrition)
    has_core = "protein_g" in nutrition and "energy_kcal" in nutrition
    if count >= 6 and has_core and serving_size:
        return "high"
    if count >= 3 or has_core:
        return "medium"
    return "low"
