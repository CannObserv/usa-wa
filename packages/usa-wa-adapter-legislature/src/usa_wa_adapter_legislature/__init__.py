"""WA State Legislature (WSL) adapter package.

Sources the Legislature's SOAP web services and its official members roster PDF into the
#304 raw store (:mod:`.raw_harvest`, :mod:`.roster_pdf.raw_harvest`), and records operator
attestations (:mod:`.operators`, :mod:`.committees.succession_cli`). The #302 pipeline
stages from the raw store through this package's pure parsers and resource ids.
"""
