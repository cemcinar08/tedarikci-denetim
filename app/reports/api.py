from fastapi import APIRouter
from fastapi.responses import Response

from .. import auth
from . import build_audit_report, report_filename, to_csv, to_pdf

router = APIRouter()


@router.get("/api/reports/audits/{audit_id}.pdf")
def audit_pdf(audit_id: int):
    r = build_audit_report(audit_id)
    auth.log("report.download", "audit", audit_id, {"format": "pdf"})
    return Response(to_pdf(r), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{report_filename(r, "pdf")}"'})


@router.get("/api/reports/audits/{audit_id}.csv")
def audit_csv(audit_id: int):
    r = build_audit_report(audit_id)
    auth.log("report.download", "audit", audit_id, {"format": "csv"})
    return Response(to_csv(r), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{report_filename(r, "csv")}"'})
