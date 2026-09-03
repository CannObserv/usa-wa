-- The matching tier's single output (#308): every exact-rule pair, one
-- vocabulary for the registrar. Splink's fuzzy tail unions in here when it
-- lands. Proposals only — this layer never writes identity. `kind` names the
-- entity kind a pair proposes (#302 CR: the registrar registers per kind —
-- an org rule unioned in without it would silently mint person entities).
select kind, left_key, right_key, rule, score from {{ ref('match_pdc_wsl') }}
union all
select kind, left_key, right_key, rule, score from {{ ref('match_roster_wsl') }}
