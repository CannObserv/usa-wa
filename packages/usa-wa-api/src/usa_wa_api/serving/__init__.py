"""The serving tier (#313): the published datasets, projected into Postgres.

`schema.py` declares the disposable tables; `load.py` fills them from
`published/`. Nothing else in the deployment writes here.
"""
