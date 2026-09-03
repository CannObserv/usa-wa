-- The matching tier's single output (#308): every exact-rule pair, one
-- vocabulary for the registrar. Splink's fuzzy tail unions in here when it
-- lands. Proposals only — this layer never writes identity.
select left_key, right_key, rule, score from {{ ref('match_pdc_wsl') }}
union all
select left_key, right_key, rule, score from {{ ref('match_roster_wsl') }}
