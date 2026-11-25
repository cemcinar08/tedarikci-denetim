"""python -m app.reports <audit_id> --format pdf|csv [--out dosya]"""
import argparse
import sys

from ..db import init_db
from . import build_audit_report, report_filename, to_csv, to_pdf

p = argparse.ArgumentParser(description="Denetim raporu üret")
p.add_argument("audit_id", type=int)
p.add_argument("--format", choices=["pdf", "csv"], default="pdf")
p.add_argument("--out")
a = p.parse_args()
init_db()
rep = build_audit_report(a.audit_id)
data = to_pdf(rep) if a.format == "pdf" else to_csv(rep)
out = a.out or report_filename(rep, a.format)
with open(out, "wb") as f:
    f.write(data)
print(out, file=sys.stderr)
