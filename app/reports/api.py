from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import auth
from . import build_audit_report, report_filename, to_csv, to_pdf
from .portfolio import build_portfolio, dora_register_csv, portfolio_pdf

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


def _internal_only():
    if auth.is_supplier_user():
        raise HTTPException(403, "Bu rapor yalnızca kurum kullanıcılarına açıktır.")


@router.get("/api/reports/portfolio.pdf")
def portfolio():
    _internal_only()
    auth.log("report.download", "portfolio", None, {"format": "pdf"})
    import datetime as dt
    return Response(portfolio_pdf(build_portfolio()), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="tedarikci-portfoy-{dt.date.today()}.pdf"'})


@router.get("/api/reports/portfolio.json")
def portfolio_json():
    _internal_only()
    return build_portfolio()


@router.get("/api/reports/dora-register.csv")
def dora_register():
    _internal_only()
    auth.log("report.download", "dora_register", None, {"format": "csv"})
    import datetime as dt
    return Response(dora_register_csv(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="dora-bilgi-kaydi-{dt.date.today()}.csv"'})
