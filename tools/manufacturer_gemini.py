"""One Gemini call: BOM manufacturer -> Mouser and DigiKey catalog names."""
import re
from pydantic import BaseModel
from langsmith import traceable


def name_key(value):
    return re.sub(r'[^a-z0-9]', '', str(value or '').casefold())


class MouserAlias(BaseModel):
    original: str
    mouser_name: str | None = None


class DigiKeyAlias(BaseModel):
    original: str
    digikey_name: str | None = None


class ManufacturerResponse(BaseModel):
    mouser_aliases: list[MouserAlias]
    digikey_aliases: list[DigiKeyAlias]

@traceable(name="Manufactuer search & normalisation", run_type="chain")
def resolve_supplier_manufacturers(bom_names, mouser_official, digikey_options,
                                   model='gemini-3.5-flash-lite', llm=None):
    """Map BOM names to supplier catalog labels; never infer a manufacturer or MPN.

    DigiKey options are *observed* manufacturer labels from MPN-relevant search
    results, not a complete official manufacturer directory. Null means unverified.
    """
    generic = {'unspecified', 'unknown', 'other', 'none', 'na'}
    official = {name_key(n): n for n in mouser_official if name_key(n) not in generic}
    names = list(dict.fromkeys(str(n or '').strip() for n in bom_names))
    mapping = {n: official.get(name_key(n)) for n in names}
    missing = [n for n in names if not mapping[n]]

    options = {}
    for name, labels in digikey_options.items():
        if name in names:
            options[name] = list(dict.fromkeys(str(x).strip() for x in labels if x))
    dk_names = {}
    for name in names:
        choices = options.get(name, [])
        dk_names[name] = next((x for x in choices if name_key(x) == name_key(name)), None)
    unresolved = {n: options[n] for n in names if n in options and options[n] and dk_names[n] is None}
    calls = 0
    if missing or unresolved:
        if llm is None:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model=model).with_structured_output(
                ManufacturerResponse, method='json_schema')
        prompt = f'''You map BOM electronics manufacturer names to supplier catalog names.

RULES:
- Work ONLY with manufacturer names. Never infer or change MPNs.
- Accept only clear aliases, corporate rebranding or established naming variants
  of the SAME manufacturer. Never accept an electrically equivalent brand.
- Do not guess. If uncertain, return null.
- Copy every original BOM manufacturer name exactly.
- Return every requested entry using the structured schema.

TASK 1 — MOUSER:
- For each unmatched manufacturer, choose one name from the official Mouser list.
- Never choose generic names such as Unspecified, Unknown or Other.
- If none match reliably, return mouser_name = null.

TASK 2 — DIGIKEY:
- For each BOM manufacturer, choose the SAME manufacturer's name from its
  observed DigiKey catalog label options (these are NOT verified aliases).
- Choose ONLY a label provided for that manufacturer; no invented names.
- If no listed label is the SAME manufacturer, return digikey_name = null.
- Do not infer identity from a matching MPN alone.

OFFICIAL MOUSER MANUFACTURERS: {mouser_official!r}
UNMATCHED MOUSER NAMES: {missing!r}
DIGIKEY OPTIONS BY BOM MANUFACTURER: {unresolved!r}'''
        response = llm.invoke(prompt)
        if isinstance(response, dict):
            response = ManufacturerResponse.model_validate(response)
        calls = 1
        for entry in response.mouser_aliases:
            if entry.original in missing:
                mapping[entry.original] = official.get(name_key(entry.mouser_name))
        for entry in response.digikey_aliases:
            if entry.original in unresolved and entry.digikey_name in unresolved[entry.original]:
                dk_names[entry.original] = entry.digikey_name
    verified = {(name, label): bool(dk_names.get(name) and name_key(label) == name_key(dk_names[name]))
                for name, labels in options.items() for label in labels}
    return mapping, verified, calls


def suggest_manufacturer_aliases(unmatched_names, official_names, model='gemini-3.5-flash-lite'):
    mapping, _, _ = resolve_supplier_manufacturers(unmatched_names, official_names, {}, model=model)
    return mapping


def resolve_from_directories(bom_names, mouser_official, digikey_official,
                             model='gemini-3.5-flash-lite', llm=None):
    """Return independent verified Mouser names and DigiKey name+IDs.

    Exact names are deterministic. One structured Gemini call only if some
    manufacturer is unresolved in at least one directory. Never accept an
    invented manufacturer label or an ID not supplied by DigiKey.
    """
    names = list(dict.fromkeys(str(n or '').strip() for n in bom_names))
    excluded = {'unspecified', 'unknown', 'other', 'none', 'na'}
    mouser_index = {name_key(n): str(n) for n in mouser_official
                    if name_key(n) not in excluded}
    dk_index = {name_key(e['name']): {'name': str(e['name']), 'id': str(e['id'])}
                for e in digikey_official if isinstance(e, dict) and e.get('name')
                and e.get('id') is not None and name_key(e['name']) not in excluded}
    mouser_map = {n: mouser_index.get(name_key(n)) for n in names}
    digikey_map = {n: dk_index.get(name_key(n)) for n in names}
    missing_mouser = [n for n in names if not mouser_map[n]]
    missing_dk = [n for n in names if not digikey_map[n]]
    calls = 0
    if missing_mouser or missing_dk:
        if llm is None:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model=model).with_structured_output(
                ManufacturerResponse, method='json_schema')
        # Manufacturer IDs are intentionally NOT passed to Gemini; IDs come
        # solely from the authoritative directory lookup after name validation.
        prompt = f"""Resolve BOM manufacturer aliases against two official supplier directories.
Rules: same legal/manufacturing brand only; no substitutes or inferred part numbers.
Use ONLY an exact name copied from the corresponding supplied directory.
Return null for uncertainty; return an entry for each unresolved name in each supplier list.
MOUSER ORIGINAL NAMES: {missing_mouser!r}
MOUSER OFFICIAL NAMES: {list(mouser_official)!r}
DIGIKEY ORIGINAL NAMES: {missing_dk!r}
DIGIKEY OFFICIAL NAMES: {[e['name'] for e in digikey_official]!r}
Return mouser_aliases and digikey_aliases using the structured schema."""
        response = llm.invoke(prompt)
        if isinstance(response, dict):
            response = ManufacturerResponse.model_validate(response)
        calls = 1
        for entry in response.mouser_aliases:
            if entry.original in missing_mouser:
                mouser_map[entry.original] = mouser_index.get(name_key(entry.mouser_name))
        for entry in response.digikey_aliases:
            if entry.original in missing_dk:
                digikey_map[entry.original] = dk_index.get(name_key(entry.digikey_name))
    return mouser_map, digikey_map, calls
