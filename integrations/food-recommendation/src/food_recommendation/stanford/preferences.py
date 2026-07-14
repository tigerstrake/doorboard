"""Dietary preferences for the Stanford dining scorer and LLM prompt.

Ported from the upstream ``config/preferences.yml`` into bundled Python data so
the integration needs no YAML dependency (ADR-0003 keeps the stack boring). The
values are the single source of truth for what is recommended and penalised; an
operator may override them at runtime by pointing ``STANFORD_PREFERENCES_PATH``
at a JSON file with the same shape.

To tune scoring, adjust ``score`` (positive = bonus, negative = penalty) or edit
``keywords``. Matching is case-insensitive against item name + ingredient text.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

DEFAULT_PREFERENCES: dict[str, Any] = {
    "description": (
        "Optimize for health and ingredient quality. Prioritize whole foods, "
        "high protein, vegetables, legumes, whole grains, fruit, nuts, yogurt, "
        "minimally processed foods, and balanced meals. Avoid ultra-processed "
        "foods, fried foods, sugary desserts, refined-carb-heavy meals, "
        "refined-carb-only meals, and low-quality processed meats."
    ),
    "notes": [
        "Used to be vegetarian but now eats meat for health reasons.",
        "Recommend meat only when minimally processed and whole — grilled "
        "chicken, turkey, lean beef, lamb, or similar.",
        "Do not recommend processed meat, breaded meat, nugget-style chicken, "
        "hot dogs, low-quality deli meats, or anything that feels like mystery meat.",
        "Do not recommend fish or seafood.",
        "Do not recommend plain or pure egg dishes such as scrambled eggs, "
        "boiled eggs, omelets, or egg-forward meals.",
        "Vegetarian options are welcome if they are nutritionally strong (tofu, "
        "lentils, beans, chickpeas, yogurt, etc.).",
    ],
    "scoring": {
        "bonuses": [
            {
                "category": "minimally_processed_meat",
                "score": 4.0,
                "keywords": [
                    "grilled chicken",
                    "roasted chicken",
                    "chicken breast",
                    "baked chicken",
                    "turkey breast",
                    "roasted turkey",
                    "grilled turkey",
                    "sirloin",
                    "tenderloin",
                    "flank steak",
                    "grass-fed beef",
                    "lean beef",
                    "lamb",
                    "bison",
                    "venison",
                ],
            },
            {
                "category": "legumes",
                "score": 3.5,
                "keywords": [
                    "lentil",
                    "chickpea",
                    "black bean",
                    "kidney bean",
                    "pinto bean",
                    "navy bean",
                    "black eyed pea",
                    "black-eyed pea",
                    "split pea",
                    "garbanzo",
                    "white bean",
                    "cannellini",
                    "fava bean",
                    "lima bean",
                    "edamame",
                ],
            },
            {
                "category": "vegetarian_protein",
                "score": 3.0,
                "keywords": [
                    "tofu",
                    "tempeh",
                    "seitan",
                    "paneer",
                    "cottage cheese",
                    "greek yogurt",
                ],
            },
            {
                "category": "vegetables",
                "score": 2.5,
                "keywords": [
                    "broccoli",
                    "spinach",
                    "kale",
                    "arugula",
                    "chard",
                    "collard",
                    "beet",
                    "carrot",
                    "zucchini",
                    "squash",
                    "asparagus",
                    "cauliflower",
                    "brussels sprout",
                    "cabbage",
                    "celery",
                    "cucumber",
                    "eggplant",
                    "green bean",
                    "bell pepper",
                    "mushroom",
                    "artichoke",
                    "fennel",
                    "bok choy",
                    "rapini",
                    "leek",
                    "steamed vegetable",
                    "roasted vegetable",
                    "seasonal vegetable",
                    "mixed vegetable",
                    "vegetable board",
                    "curried vegetable",
                    "sauteed vegetable",
                ],
            },
            {
                "category": "whole_grains",
                "score": 2.0,
                "keywords": [
                    "brown rice",
                    "wild rice",
                    "quinoa",
                    "farro",
                    "barley",
                    "bulgur",
                    "whole wheat",
                    "whole grain",
                    "oat",
                    "millet",
                    "buckwheat",
                    "amaranth",
                    "teff",
                    "freekeh",
                    "spelt",
                    "wheatberry",
                ],
            },
            {
                "category": "fruit",
                "score": 1.5,
                "keywords": [
                    "apple",
                    "banana",
                    "orange",
                    "berries",
                    "blueberry",
                    "strawberry",
                    "raspberry",
                    "blackberry",
                    "mango",
                    "pineapple",
                    "melon",
                    "peach",
                    "plum",
                    "pear",
                    "grape",
                    "kiwi",
                    "citrus",
                    "avocado",
                    "pomegranate",
                    "cherry",
                    "papaya",
                    "guava",
                ],
            },
            {
                "category": "nuts_seeds",
                "score": 1.5,
                "keywords": [
                    "almond",
                    "walnut",
                    "cashew",
                    "pecan",
                    "pistachio",
                    "peanut",
                    "sunflower seed",
                    "pumpkin seed",
                    "chia seed",
                    "flaxseed",
                    "hemp seed",
                    "tahini",
                    "sesame",
                    "nut butter",
                ],
            },
            {
                "category": "yogurt",
                "score": 1.5,
                "keywords": ["greek yogurt", "yogurt", "kefir"],
            },
            {
                "category": "healthy_fats",
                "score": 1.0,
                "keywords": ["olive oil", "avocado oil", "flaxseed oil"],
            },
            {
                "category": "healthy_soup",
                "score": 0.5,
                "keywords": [
                    "broth",
                    "minestrone",
                    "lentil soup",
                    "vegetable soup",
                    "miso soup",
                ],
            },
        ],
        "penalties": [
            {
                "category": "fish_seafood",
                "score": -6.0,
                "keywords": [
                    "salmon",
                    "tuna",
                    "tilapia",
                    "cod",
                    "halibut",
                    "trout",
                    "mahi",
                    "flounder",
                    "bass",
                    "snapper",
                    "catfish",
                    "shrimp",
                    "crab",
                    "lobster",
                    "oyster",
                    "clam",
                    "scallop",
                    "mussel",
                    "squid",
                    "calamari",
                    "seafood",
                    "fish",
                    "anchovy",
                    "sardine",
                    "herring",
                    "blackened salmon",
                    "seared salmon",
                ],
            },
            {
                "category": "ultra_processed_meat",
                "score": -6.0,
                "keywords": [
                    "hot dog",
                    "sausage link",
                    "pepperoni",
                    "salami",
                    "bologna",
                    "spam",
                    "bacon",
                    "canadian bacon",
                    "pastrami",
                    "corned beef",
                    "luncheon meat",
                    "deli meat",
                    "mystery meat",
                ],
            },
            {
                "category": "breaded_processed_meat",
                "score": -5.0,
                "keywords": [
                    "nugget",
                    "chicken nugget",
                    "chicken tender",
                    "chicken finger",
                    "chicken strip",
                    "breaded chicken",
                    "breaded fish",
                    "popcorn chicken",
                    "chicken patty",
                    "meat lover",
                ],
            },
            {
                "category": "pure_egg_dishes",
                "score": -4.5,
                "keywords": [
                    "scrambled eggs",
                    "scrambled egg",
                    "fried egg",
                    "over easy",
                    "sunny side",
                    "poached egg",
                    "hard boiled egg",
                    "soft boiled egg",
                    "deviled egg",
                    "omelette",
                    "omelet",
                    "frittata",
                    "quiche",
                    "egg casserole",
                    "egg scramble",
                ],
            },
            {
                "category": "fried_foods",
                "score": -4.0,
                "keywords": [
                    "fried chicken",
                    "deep fried",
                    "french fries",
                    "tater tot",
                    "onion ring",
                    "fritter",
                    "churro",
                    "donut",
                    "doughnut",
                    "fried rice",
                    "tempura",
                ],
            },
            {
                "category": "sugary_desserts",
                "score": -3.5,
                "keywords": [
                    "cake",
                    "cupcake",
                    "cookie",
                    "brownie",
                    "muffin",
                    "pastry",
                    "pie",
                    "ice cream",
                    "gelato",
                    "sorbet",
                    "pudding",
                    "candy",
                    "chocolate sauce",
                    "caramel",
                    "syrup",
                    "waffle",
                    "pancake",
                    "crepe",
                    "danish",
                    "croissant",
                    "scone",
                    "tart",
                    "dessert",
                    "cobbler",
                    "cheesecake",
                ],
            },
            {
                "category": "low_quality_food",
                "score": -3.0,
                "keywords": [
                    "mac and cheese",
                    "macaroni and cheese",
                    "velveeta",
                    "alfredo",
                    "nacho",
                    "chips",
                    "cheese sauce",
                    "queso",
                ],
            },
            {
                "category": "refined_carbs",
                "score": -2.0,
                "keywords": [
                    "white bread",
                    "white rice",
                    "white pasta",
                    "bagel",
                    "baguette",
                    "grits",
                    "penne pasta",
                    "spaghetti",
                    "macaroni",
                    "linguine",
                    "fettuccine",
                    "ramen",
                    "instant noodle",
                ],
            },
        ],
    },
}


def _validate(data: dict[str, Any]) -> None:
    required = {"description", "notes", "scoring"}
    missing = required - set(data)
    if missing:
        raise ValueError(f"preferences is missing required keys: {missing}")
    scoring = data.get("scoring", {})
    if "bonuses" not in scoring or "penalties" not in scoring:
        raise ValueError("preferences scoring section must have 'bonuses' and 'penalties'")
    for rule in list(scoring["bonuses"]) + list(scoring["penalties"]):
        for field in ("category", "score", "keywords"):
            if field not in rule:
                raise ValueError(f"Each scoring rule must have '{field}'. Offending rule: {rule}")


def load_preferences(path: str | None = None) -> dict[str, Any]:
    """Return the preferences dict.

    With no ``path``, returns a deep copy of :data:`DEFAULT_PREFERENCES`. With a
    ``path``, loads and validates a JSON override of the same shape.
    """
    if path:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        _validate(raw)
        return raw
    return copy.deepcopy(DEFAULT_PREFERENCES)
