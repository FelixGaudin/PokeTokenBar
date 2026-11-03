"""Collectible alternate forms: Unown's letters, Rotom's appliances, Shaymin's Sky Forme…

A form belongs to the individual and is drawn once for its whole line. Each species of
the line shows it when it has that form and its default otherwise: a Sandy Burmy that
evolves into Mothim keeps "sandy" on record but looks like Mothim. Values are PokéAPI
form identifiers. Only forms with an animated Black/White sprite are listed, the same
source that bounds hatchable species; gender differences are not forms.

Forms change appearance, names and collection slots. Types, stats and moves keep the
species' default-form data.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .balance import collection_weight
from .pokeapi import LANG_FALLBACKS


@dataclass(frozen=True)
class FormEntry:
    form: str
    # PokéAPI `pokemon-form` resource holding the localized form names.
    form_id: int
    # PokéAPI variety whose static PNG shows this form (10006.png for Sky Shaymin);
    # None when the static sprite is named like the animated one (412-sandy.png).
    pokemon_id: int | None = None


def _forms(*entries: tuple[str, int] | tuple[str, int, int]) -> list[FormEntry]:
    return [FormEntry(*e) for e in entries]


UNOWN_SPECIES_ID = 201
_UNOWN_LETTERS = [chr(c) for c in range(ord("a"), ord("z") + 1)] + ["exclamation", "question"]

# Every species lists its default form first; that one keeps the plain <id> sprites.
CATALOG: dict[int, list[FormEntry]] = {
    UNOWN_SPECIES_ID: [
        FormEntry(f, UNOWN_SPECIES_ID if i == 0 else 10000 + i)
        for i, f in enumerate(_UNOWN_LETTERS)
    ],
    351: _forms(
        ("normal", 351), ("sunny", 10028, 10013), ("rainy", 10029, 10014), ("snowy", 10030, 10015)
    ),
    386: _forms(
        ("normal", 386),
        ("attack", 10031, 10001),
        ("defense", 10032, 10002),
        ("speed", 10033, 10003),
    ),
    412: _forms(("plant", 412), ("sandy", 10034), ("trash", 10035)),
    413: _forms(("plant", 413), ("sandy", 10036, 10004), ("trash", 10037, 10005)),
    421: _forms(("overcast", 421), ("sunshine", 10038)),
    422: _forms(("west", 422), ("east", 10039)),
    423: _forms(("west", 423), ("east", 10040)),
    479: _forms(
        ("normal", 479),
        ("heat", 10058, 10008),
        ("wash", 10059, 10009),
        ("frost", 10060, 10010),
        ("fan", 10061, 10011),
        ("mow", 10062, 10012),
    ),
    487: _forms(("altered", 487), ("origin", 10063, 10007)),
    492: _forms(("land", 492), ("sky", 10064, 10006)),
    493: _forms(
        ("normal", 493),
        ("fighting", 10045),
        ("flying", 10047),
        ("poison", 10052),
        ("ground", 10050),
        ("rock", 10054),
        ("bug", 10041),
        ("ghost", 10048),
        ("steel", 10055),
        ("fire", 10046),
        ("water", 10056),
        ("grass", 10049),
        ("electric", 10044),
        ("psychic", 10053),
        ("ice", 10051),
        ("dragon", 10043),
        ("dark", 10042),
        ("fairy", 10085),
    ),
    550: _forms(("red-striped", 550), ("blue-striped", 10066, 10016)),
    555: _forms(("standard", 555), ("zen", 10067, 10017)),
    585: _forms(("spring", 585), ("summer", 10068), ("autumn", 10069), ("winter", 10070)),
    586: _forms(("spring", 586), ("summer", 10071), ("autumn", 10072), ("winter", 10073)),
    641: _forms(("incarnate", 641), ("therian", 10079, 10019)),
    642: _forms(("incarnate", 642), ("therian", 10080, 10020)),
    645: _forms(("incarnate", 645), ("therian", 10081, 10021)),
    646: _forms(("normal", 646), ("black", 10082, 10022), ("white", 10083, 10023)),
    647: _forms(("ordinary", 647), ("resolute", 10084, 10024)),
    648: _forms(("aria", 648), ("pirouette", 10074, 10018)),
    649: _forms(
        ("normal", 649), ("douse", 10075), ("shock", 10076), ("burn", 10077), ("chill", 10078)
    ),
}

# Catalogued species whose line starts elsewhere. The form is drawn at hatch, so Cherubi
# and Darumaka eggs already know which Cherrim or Darmanitan they will become.
_LINE_BASE = {413: 412, 421: 420, 423: 422, 555: 554, 586: 585}

# Castform, Rotom, Kyurem and Genesect have no official name for their usual look.
_UNNAMED_DEFAULT = {351, 479, 646, 649}
NORMAL_FORM = {
    "en": "Normal Form",
    "ko": "노말폼",
    "ja": "ノーマルフォルム",
    "ja-Hrkt": "ノーマルフォルム",
    "es": "Forma Normal",
    "fr": "Forme Normale",
    "de": "Normalform",
}


def forms_of(species_id: int) -> list[str]:
    """The species' forms, default first; empty when it has a single appearance."""
    return [e.form for e in CATALOG.get(species_id, [])]


def form_species(base_id: int) -> list[int]:
    """Species of the line that change appearance with the individual's form."""
    return sorted(s for s in CATALOG if _LINE_BASE.get(s, s) == base_id)


def line_forms(base_id: int) -> list[str]:
    """Everything an individual of this line can carry, default first."""
    return list(dict.fromkeys(f for s in form_species(base_id) for f in forms_of(s)))


def resolve(species_id: int, form: str | None) -> str | None:
    """The form this species shows: the individual's own when it has it, else its default."""
    forms = forms_of(species_id)
    if not forms:
        return None
    return form if form in forms else forms[0]


def resolve_line(base_id: int, form: str | None) -> str | None:
    """The form an individual stores: None for lines without forms, and the line default
    for records written before the line had forms (older Unown saves keep their A)."""
    forms = line_forms(base_id)
    if not forms:
        return None
    return form if form in forms else forms[0]


def is_default(species_id: int, form: str | None) -> bool:
    forms = forms_of(species_id)
    return not forms or resolve(species_id, form) == forms[0]


def roll(r: int, forms: list[str], collected: set[str]) -> str:
    """Drawn after the species: uncollected forms weigh 2, collected ones 1."""
    weights = [collection_weight(2, f in collected) for f in forms]
    remaining = r % sum(weights)
    for form, weight in zip(forms[:-1], weights[:-1], strict=True):
        remaining -= weight
        if remaining < 0:
            return form
    return forms[-1]


def collected_line_forms(base_id: int, owned: set[tuple[int, str]]) -> set[str]:
    """Forms of the line that count as collected for the draw.

    Where several species show a form, it counts only once every one of them is owned in
    it: a Sandy Burmy that became Mothim does not yet complete Sandy Wormadam.
    """
    species = form_species(base_id)
    return {
        f for f in line_forms(base_id) if all((s, f) in owned for s in species if f in forms_of(s))
    }


def _entry(species_id: int, form: str | None) -> FormEntry | None:
    resolved = resolve(species_id, form)
    return next((e for e in CATALOG.get(species_id, []) if e.form == resolved), None)


def sprite_name(species_id: int, form: str | None) -> str:
    """Animated GIF and cache file stem: <id>-<form>, the default keeping the plain <id>."""
    if is_default(species_id, form):
        return str(species_id)
    return f"{species_id}-{resolve(species_id, form)}"


def static_sprite_name(species_id: int, form: str | None) -> str:
    """Static PNG stem: forms that are separate PokéAPI varieties live under that id."""
    entry = _entry(species_id, form)
    if entry is not None and entry.pokemon_id is not None:
        return str(entry.pokemon_id)
    return sprite_name(species_id, form)


def unown_symbol(form: str | None) -> str:
    resolved = resolve(UNOWN_SPECIES_ID, form) or "a"
    return {"exclamation": "!", "question": "?"}.get(resolved, resolved.upper())


def name_resource(species_id: int, form: str | None) -> int | None:
    """PokéAPI `pokemon-form` id with this form's localized name. Unown and the unnamed
    usual looks need none."""
    if species_id == UNOWN_SPECIES_ID:
        return None
    if species_id in _UNNAMED_DEFAULT and is_default(species_id, form):
        return None
    entry = _entry(species_id, form)
    return entry.form_id if entry is not None else None


def name_resources(species_id: int) -> list[int]:
    """Every name resource of the species, for pickers that label all of its forms."""
    ids = (name_resource(species_id, f) for f in forms_of(species_id))
    return [i for i in ids if i is not None]


def identifier(form: str) -> str:
    """The label shown until PokéAPI answers: "blue-striped" -> "Blue Striped"."""
    return " ".join(part[:1].upper() + part[1:] for part in form.split("-"))


def label(
    species_id: int,
    form: str | None,
    lang: str,
    names: Callable[[int], Mapping[str, str] | None],
) -> str | None:
    """Localized form name ("Forme Céleste"), Unown's letter, or the identifier until the
    names are cached. None for species without forms."""
    resolved = resolve(species_id, form)
    if resolved is None:
        return None
    if species_id == UNOWN_SPECIES_ID:
        return unown_symbol(resolved)
    if species_id in _UNNAMED_DEFAULT and is_default(species_id, resolved):
        return NORMAL_FORM.get(lang, NORMAL_FORM["en"])
    resource = name_resource(species_id, resolved)
    by_lang = (names(resource) if resource is not None else None) or {}
    for code in (*LANG_FALLBACKS.get(lang, (lang,)), "en"):
        if by_lang.get(code):
            return by_lang[code]
    return identifier(resolved)


def display_name(name: str, species_id: int, form: str | None, label: str | None = None) -> str:
    """Unown always names its letter; other species name only a non-default form.

    Labels one thumbnail wide (catch log, recap) pass no label: their sprite already
    shows the form.
    """
    if resolve(species_id, form) is None:
        return name
    if species_id == UNOWN_SPECIES_ID:
        return f"{name} [{unown_symbol(form)}]"
    if is_default(species_id, form) or not label:
        return name
    return f"{name} [{label}]"
