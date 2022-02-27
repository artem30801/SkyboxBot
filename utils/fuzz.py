from rapidfuzz import process, fuzz


# TODO maybe this needs to be async as heavy fuzzy matching may freeze up evenet loop
def fuzzy_autocomplete(query, choices):
    results = process.extract(query, choices, scorer=fuzz.WRatio, limit=25)

    return results


def fuzzy_find(query, choices):
    result = process.extractOne(query, choices, scorer=fuzz.WRatio, score_cutoff=70)

    return result[0] if result is not None else None
