-- Every seat pair that MAY overlap, by name (#501 D7): the may tier of
-- `seat_overlap_pairs()`, over the outer bounds `[valid_from, valid_to)`.
--
-- This is the like-for-like cross-check with power-map, which stores outer bounds
-- only and so applies exactly this tier (power-map review, 2026-10-17). It lists
-- every pair — accepted ones included — because PM's ratchet counts them all;
-- `store_failures` keeps the list in the build, where a pair on one side only
-- means the predicates diverged or a PM admin overlay differs (power-map#639).
--
-- WARN, and INFORMATIONAL (#501 correction 2): the may tier is never empty — a
-- succession dated only to a month, or not at all, may overlap by the width of
-- what is unknown — so `build_warnings` names this node as informational, logs
-- its count and does not fail the stage on it. The certain tier is the gate.
{{ config(severity='warn', store_failures=true) }}
select o.*
from ({{ seat_overlap_pairs('may') }}) o
