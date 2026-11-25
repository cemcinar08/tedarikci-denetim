"""Denetim raporu servisi — uygulamanın geri kalanından bağımsız modül.

    build_audit_report(audit_id) -> dict      rapor verisi (tek kaynak)
    to_csv(report) -> bytes                   madde bazlı CSV (Excel uyumlu, UTF-8 BOM, ';')
    to_pdf(report) -> bytes                   PDF (fpdf2 + gömülü DejaVu fontu, Türkçe karakter destekli)

HTTP:  GET /api/reports/audits/{id}.pdf | .csv     (report:read yetkisi + tedarikçi izolasyonu)
CLI:   python -m app.reports <audit_id> --format pdf|csv [--out dosya]
"""
from .builder import build_audit_report, report_filename  # noqa: F401
from .csv_export import to_csv  # noqa: F401
from .pdf_export import to_pdf  # noqa: F401
