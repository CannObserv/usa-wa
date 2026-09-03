-- PDC winner ↔ WSL sponsor exact rule (#308): same seat, same seating
-- biennium, sponsor surname among the filer-name tokens (PDC renders names in
-- BOTH orders — "FAGAN SUSAN K" and "David V. Taylor" — so the match is
-- token-containment, never positional). Term-OVERLAP by construction: both
-- sides are pinned to the same biennium, the spec's Jr/Sr guard. Recall is
-- deliberately partial (multi-token surnames miss); the seeded registry
-- already carries the historical links, so this rule only needs to catch the
-- forward flow — the fuzzy tail is Splink's, later. Every column is cast:
-- an empty store materializes object columns as INTEGER, and the hermetic
-- commit gate must still bind.
with pdc as (
    select
        cast(person_id as varchar) as person_id,
        cast(filer_name as varchar) as filer_name,
        cast(chamber as varchar) as chamber,
        try_cast(legislative_district as integer) as district_num,
        -- even year seats the NEXT (odd-start) biennium; an odd-year special
        -- seats the running one
        case
            when try_cast(election_year as integer) % 2 = 0
                then try_cast(election_year as integer) + 1
            else try_cast(election_year as integer)
        end as start_year
    from {{ ref('stg_pdc_winners') }}
    where person_id is not null and legislative_district is not null
)

select distinct
    'person' as kind,
    'wa_pdc:' || p.person_id as left_key,
    'usa_wa_legislature:' || cast(s.member_id as varchar) as right_key,
    'pdc_wsl_seat_surname' as rule,
    0.95 as score
from pdc p
join {{ ref('stg_wsl_sponsors') }} s
    on cast(s.biennium as varchar)
        = cast(p.start_year as varchar) || '-' || substr(cast(p.start_year + 1 as varchar), 3, 2)
    and lower(cast(s.agency as varchar)) = lower(p.chamber)
    and try_cast(s.district as integer) = p.district_num
    and list_contains(string_split(lower(p.filer_name), ' '), lower(cast(s.last_name as varchar)))
