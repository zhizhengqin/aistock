"""F-03-06 导出 PDF 报告 — 排版完整、中文无乱码。"""
from tests.conftest import client, auth_client, seed_user, fake_redis, test_db


def _sample_report():
    return {
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "stock_info": {
            "price": 1688.0, "change_pct": 1.23, "pe_ttm": 28.5,
            "pb": 9.1, "market_cap": 21200.0, "industry": "白酒",
        },
        "indicators": {"ma": {"ma5": 1670}, "rsi": {"rsi14": 55.2}},
        "analysts": {
            "technical": {"score": 7, "suggestion": "偏多", "summary": "均线多头排列"},
            "fundamental": {"score": 8, "suggestion": "买入", "summary": "ROE 稳定"},
        },
        "decision": {
            "rating": "买入", "target_price": 1900.0, "stop_loss": 1500.0,
            "confidence": 0.78, "entry_range": "1650-1700", "take_profit": "1900",
            "holding_period": "3-6个月", "position_size": "10-15%",
            "risk_warning": "消费复苏不及预期",
            "key_watchpoints": ["季度财报", "批价走势"],
            "meeting_summary": "多数分析师看多，注意估值水平。",
        },
        "disclaimer": "本分析仅供参考，不构成任何投资建议。",
        "analyzed_at": "2026-08-08T09:30:00+00:00",
    }


def _seed_report(db, user_id, code="600519"):
    from app.models.analysis_report import AnalysisReport
    r = AnalysisReport(
        user_id=user_id, stock_code=code, stock_name="贵州茅台",
        rating="买入", confidence=0.78, report_json=_sample_report(),
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r.id


# --- unit: PDF builder -------------------------------------------------------

def test_build_pdf_returns_valid_pdf_bytes():
    from app.services.report_pdf import build_analysis_pdf
    pdf = build_analysis_pdf(_sample_report())
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 2000          # real layout, not an empty stub
    assert b"%%EOF" in pdf


def test_build_pdf_handles_missing_sections():
    from app.services.report_pdf import build_analysis_pdf
    minimal = {"stock_code": "000001", "stock_name": "平安银行", "decision": {}}
    pdf = build_analysis_pdf(minimal)
    assert pdf.startswith(b"%PDF")


# --- API ---------------------------------------------------------------------

def test_pdf_endpoint_download(client, auth_client, seed_user, test_db):
    _, TestingSession = test_db
    db = TestingSession()
    report_id = _seed_report(db, seed_user["id"])
    db.close()

    resp = auth_client.get(f"/api/stocks/user/results/{report_id}/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers.get("content-disposition", "")
    assert "600519" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF")


def test_pdf_endpoint_404_for_missing(client, auth_client):
    resp = auth_client.get("/api/stocks/user/results/99999/pdf")
    assert resp.status_code == 404


def test_pdf_endpoint_404_for_other_user(client, auth_client, seed_user, test_db):
    from app.core.security import hash_password, create_access_token
    from app.models.user import User
    _, TestingSession = test_db
    db = TestingSession()
    other = User(username="other", email="o@example.com",
                 password_hash=hash_password("Passw0rd!"), role="user",
                 tier="free", is_active=True)
    db.add(other)
    db.commit()
    db.refresh(other)
    report_id = _seed_report(db, other.id)
    db.close()

    # seed_user's token must not see other's report
    resp = auth_client.get(f"/api/stocks/user/results/{report_id}/pdf")
    assert resp.status_code == 404


def test_pdf_endpoint_requires_auth(client, seed_user, test_db):
    _, TestingSession = test_db
    db = TestingSession()
    report_id = _seed_report(db, seed_user["id"])
    db.close()
    resp = client.get(f"/api/stocks/user/results/{report_id}/pdf")
    assert resp.status_code in (401, 403)
