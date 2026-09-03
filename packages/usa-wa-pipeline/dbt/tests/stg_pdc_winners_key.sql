-- (chamber, election_year, filer_id): one filer per winner cohort
select chamber, election_year, filer_id, count(*) as n
from {{ ref('stg_pdc_winners') }}
group by 1, 2, 3
having count(*) > 1
