"""Party vocabulary (#189) and the historical roster tokens (#227).

``canonicalize_party`` folds only R/D, and ``None`` means *no party Assignment* — so the
roster's seven historical abbreviations would have been silently dropped, taking 166
member-year records with them. That is the failure mode the never-silently-drop rule exists
to prevent, and it would have looked like a clean run.
"""

import pytest

from usa_wa_common.parties import (
    PARTY_DECLINED,
    PARTY_RESOLVED,
    PARTY_SLUGS,
    PARTY_UNRECOGNIZED,
    canonicalize_party,
    resolve_party_token,
    sos_party_slug,
    tally_party_tokens,
)


@pytest.mark.parametrize(
    ("token", "slug"),
    [
        ("R", "republican"),
        ("D", "democratic"),
        ("P.P.", "peoples"),
        ("Pop.", "populist"),
        ("Silver Rep.", "silver-republican"),
        ("F.L.", "farmer-labor"),
        ("S", "socialist"),
    ],
)
def test_roster_token_resolves_to_its_party_slug(token, slug):
    """Every year-independent roster token folds to the slug PM's ``org_wa_party``
    identifier carries (power-map#270/#442), so the Org is addressable by identifier rather
    than by name-match or ULID."""
    result = resolve_party_token(token, year=1897)
    assert result.slug == slug
    assert result.disposition == PARTY_RESOLVED


def test_every_resolved_slug_is_in_the_declared_vocabulary():
    """``PARTY_SLUGS`` is the contract with PM's six historical Orgs plus the two majors —
    a slug the resolver can emit but the vocabulary does not declare would have no Org."""
    tokens = ["R", "D", "P.P.", "Pop.", "Silver Rep.", "F.L.", "S", "Prog."]
    slugs = {resolve_party_token(t, year=1915).slug for t in tokens}
    assert slugs <= PARTY_SLUGS
    assert PARTY_SLUGS == {
        "republican",
        "democratic",
        "peoples",
        "populist",
        "progressive",
        "silver-republican",
        "farmer-labor",
        "socialist",
    }


@pytest.mark.parametrize("year", [1913, 1915, 1917])
def test_progressive_resolves_inside_the_org_lifespan(year):
    """The Progressive Org covers the Roosevelt/Bull Moose formation only — 1913 (38
    legislators), 1915 (10), 1917 (1 senator), none by 1919 (power-map#442)."""
    result = resolve_party_token("Prog.", year=year)
    assert result.slug == "progressive"
    assert result.disposition == PARTY_RESOLVED


def test_progressive_declines_outside_the_org_lifespan():
    """The lone 1927 ``Prog.`` row is Knute Hill, seated a decade after that body dissolved —
    he ran Farmer-Labor for Congress in 1920 and 1924 and entered Congress a Democrat in 1933.
    Mapping him onto the Progressive Org would assert a membership that never existed, so the
    token is year-scoped rather than a flat lookup (power-map#442)."""
    result = resolve_party_token("Prog.", year=1927)
    assert result.slug is None
    assert result.disposition == PARTY_DECLINED
    assert result.reason == "outside_org_lifespan"


def test_year_scoped_token_without_a_year_is_unrecognized_not_dropped():
    """A caller with no session year cannot decide ``Prog.`` — so it surfaces as a tally
    rather than resolving to a plausible-looking ``None``. ``year`` is keyword-only and
    required precisely so this is an explicit statement of ignorance, never an omission."""
    result = resolve_party_token("Prog.", year=None)
    assert result.slug is None
    assert result.disposition == PARTY_UNRECOGNIZED
    assert result.reason == "year_required"


def test_citizen_is_declined_as_a_ballot_label():
    """``Cit.`` was not a formally organised state party — the 1907 Jefferson County pair
    identified as a Republican and a Democrat once seated, and municipal archives show
    "Citizens Party" as a hyper-local ballot label. A ballot label is not an organisation, so
    there is no Org to attach to (power-map#442)."""
    result = resolve_party_token("Cit.", year=1899)
    assert result.slug is None
    assert result.disposition == PARTY_DECLINED
    assert result.reason == "ballot_label"


@pytest.mark.parametrize("token", ["Independent", "Ind.", None, "", "   "])
def test_unaffiliated_is_declined_not_unrecognized(token):
    """Independence is the *absence* of affiliation, not a party (power-map#270) — a decision,
    so it declines rather than reading as an unknown token needing investigation."""
    result = resolve_party_token(token, year=1899)
    assert result.slug is None
    assert result.disposition == PARTY_DECLINED
    assert result.reason == "unaffiliated"


def test_unknown_token_is_unrecognized_with_the_token_preserved():
    """The guardrail this issue exists to enforce: a token nobody has classified must be
    declined **with a tally**, never folded to ``None`` by default. A future roster edition
    introducing a new abbreviation must fail loudly, not run clean."""
    result = resolve_party_token("Whig", year=1889)
    assert result.slug is None
    assert result.disposition == PARTY_UNRECOGNIZED
    assert result.reason == "unknown_token"
    assert result.token == "Whig"


@pytest.mark.parametrize("token", [" p.p. ", "P.P.", "p.P."])
def test_token_matching_tolerates_case_and_surrounding_space(token):
    """The roster is OCR-adjacent text; matching on an exact upstream string is the thing
    AGENTS.md forbids. Case and edge whitespace fold; interior punctuation is significant."""
    assert resolve_party_token(token, year=1895).slug == "peoples"


def test_canonicalize_party_keeps_its_wire_domain():
    """#227 deliberately does NOT widen ``canonicalize_party``. It folds *wire* values from
    WSL and the SOS ballot, and ``sos_party_slug`` splits its input on whitespace and
    punctuation before calling it — so a historical single-letter token like ``S`` in the
    shared map would make a stray ``S`` anywhere in a ballot string resolve to Socialist.
    The historical vocabulary is reached through ``resolve_party_token`` instead."""
    assert canonicalize_party("S") is None
    assert canonicalize_party("Pop.") is None
    assert canonicalize_party("R") == "republican"
    assert sos_party_slug("(Prefers S Party)") is None


# --- the acceptance oracle: zero silent drops (#227) -------------------------

#: The full party-token census of the archived roster (rev. 2025-06-05), measured from #225's
#: production parser: 8,517 member-year records, 166 of them minor-party. Held here as data so
#: the oracle is a fast deterministic test rather than one needing the production archive.
ROSTER_CENSUS = {
    ("R", 1897): 4554,
    ("D", 1897): 3797,
    ("P.P.", 1895): 50,
    ("Pop.", 1897): 49,
    ("Prog.", 1913): 46,
    ("Prog.", 1927): 1,
    ("Silver Rep.", 1897): 11,
    ("F.L.", 1921): 7,
    ("Cit.", 1899): 1,
    ("S", 1913): 1,
}


def _census_pairs():
    for (token, year), n in ROSTER_CENSUS.items():
        for _ in range(n):
            yield (token, year)


def test_oracle_every_roster_record_resolves_or_is_counted():
    """#227's acceptance oracle. Every one of the roster's 8,517 member-year records must
    resolve to a party Org or be explicitly declined and counted — zero silent drops. An
    unrecognized token here means a future edition introduced an abbreviation nobody has
    classified, which must fail the build rather than quietly emit 166 fewer spans."""
    tally = tally_party_tokens(_census_pairs())

    assert tally.total == 8517
    assert tally.clean, f"unclassified roster party tokens: {dict(tally.unrecognized)}"
    assert not tally.unrecognized


def test_oracle_accounts_for_all_166_minor_party_records():
    """The split the oracle is really about: 164 of the 166 minor-party records reach an Org,
    and the 2 that do not are the two power-map#442 adjudicated away — Knute Hill's 1927
    ``Prog.`` row and the 1899 ``Cit.`` ballot label. Both are counted, neither is dropped."""
    tally = tally_party_tokens(_census_pairs())

    assert tally.resolved["peoples"] == 50
    assert tally.resolved["populist"] == 49
    assert tally.resolved["progressive"] == 46
    assert tally.resolved["silver-republican"] == 11
    assert tally.resolved["farmer-labor"] == 7
    assert tally.resolved["socialist"] == 1
    minor = sum(tally.resolved[s] for s in PARTY_SLUGS if s not in {"republican", "democratic"})
    assert minor == 164

    assert tally.declined["outside_org_lifespan"] == 1  # Knute Hill, 1927
    assert tally.declined["ballot_label"] == 1  # the 1899 Citizen row
    assert minor + sum(tally.declined.values()) == 166
