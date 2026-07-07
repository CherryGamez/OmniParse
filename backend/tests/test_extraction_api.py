"""
End-to-end backend tests for the Document Intelligence Platform.

Covers:
 - /api/health and /api/ready
 - /api/v1/auth/token mint
 - /api/v1/extract/sync (json s3Uri + multipart upload)
 - Auth (401) and RBAC (403) enforcement with RFC 7807 problem+json
 - Missing-source validation (400)
 - X-Correlation-Id echo
 - /api/v1/extract/async + /api/v1/jobs/{id} polling, plus 404
"""
import io
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://multilang-paddle-ocr.preview.emergentagent.com",
).rstrip("/")

API = f"{BASE_URL}/api/v1"
SYNC_TIMEOUT = 60
ASYNC_POLL_TIMEOUT = 45


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    return s


@pytest.fixture(scope="session")
def token(session):
    r = session.post(
        f"{API}/auth/token",
        json={"sub": "test-user", "roles": ["extractor", "admin"]},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "accessToken" in data and isinstance(data["accessToken"], str)
    assert data["tokenType"] == "Bearer"
    return data["accessToken"]


@pytest.fixture(scope="session")
def viewer_token(session):
    r = session.post(
        f"{API}/auth/token",
        json={"sub": "viewer-user", "roles": ["viewer"]},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    return r.json()["accessToken"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _assert_mock_result(result):
    """Shared assertions for a mock-mode extraction result."""
    assert result.get("mock") is True
    assert result.get("model") == "mock"
    assert isinstance(result.get("markdown"), str) and len(result["markdown"]) > 0
    assert isinstance(result.get("structured"), dict) and len(result["structured"]) > 0


def _poll_until_terminal(session, token, job_id):
    """Poll a job until COMPLETED/FAILED (or timeout); return the final body."""
    deadline = time.time() + ASYNC_POLL_TIMEOUT
    final = None
    while time.time() < deadline:
        jr = session.get(f"{API}/jobs/{job_id}", headers=_auth(token), timeout=15)
        assert jr.status_code == 200, jr.text
        final = jr.json()
        if final["status"] in ("COMPLETED", "FAILED"):
            break
        time.sleep(1.5)
    return final


# ---------------------------------------------------------------------------
# Health & Ready
# ---------------------------------------------------------------------------
def test_api_health(session):
    r = session.get(f"{BASE_URL}/api/health", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "healthy"


def test_api_ready(session):
    r = session.get(f"{BASE_URL}/api/ready", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ready"
    assert data["checks"].get("database") == "ok"


# ---------------------------------------------------------------------------
# Auth & RBAC
# ---------------------------------------------------------------------------
def test_auth_token_mint(session):
    r = session.post(
        f"{API}/auth/token",
        json={"sub": "abc", "roles": ["extractor"]},
        timeout=10,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["accessToken"].count(".") == 2  # JWT format
    assert data["expiresIn"] == 3600
    assert "extractor" in data["roles"]


def test_sync_no_auth_returns_401_problem_json(session):
    cid = f"test-noauth-{uuid.uuid4().hex[:8]}"
    r = session.post(
        f"{API}/extract/sync",
        json={"s3Uri": "s3://demo-bucket/contracts/invoice-001.pdf"},
        headers={"X-Correlation-Id": cid},
        timeout=15,
    )
    assert r.status_code == 401
    assert "application/problem+json" in r.headers.get("content-type", "")
    body = r.json()
    assert body.get("correlationId") == cid
    assert r.headers.get("X-Correlation-Id") == cid


def test_sync_viewer_role_returns_403_problem_json(session, viewer_token):
    cid = f"test-rbac-{uuid.uuid4().hex[:8]}"
    r = session.post(
        f"{API}/extract/sync",
        json={"s3Uri": "s3://demo-bucket/contracts/invoice-001.pdf"},
        headers={**_auth(viewer_token), "X-Correlation-Id": cid},
        timeout=15,
    )
    assert r.status_code == 403
    assert "application/problem+json" in r.headers.get("content-type", "")
    body = r.json()
    assert body.get("correlationId") == cid


def test_sync_missing_source_returns_400(session, token):
    r = session.post(
        f"{API}/extract/sync",
        json={"instructions": "extract anything"},
        headers=_auth(token),
        timeout=15,
    )
    assert r.status_code == 400
    assert "application/problem+json" in r.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# SYNC extraction
# ---------------------------------------------------------------------------
def test_sync_extract_s3_uri_mock_fallback(session, token):
    cid = f"test-sync-s3-{uuid.uuid4().hex[:8]}"
    payload = {
        "s3Uri": "s3://demo-bucket/contracts/invoice-001.pdf",
        "instructions": "Extract document type, header fields, line items and totals.",
    }
    r = session.post(
        f"{API}/extract/sync",
        json=payload,
        headers={**_auth(token), "X-Correlation-Id": cid},
        timeout=SYNC_TIMEOUT,
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("X-Correlation-Id") == cid
    result = r.json()["result"]
    _assert_mock_result(result)
    # Mock extractor schema keys
    for key in ("documentType", "title", "sections", "fields", "summary"):
        assert key in result["structured"], f"Missing structured key: {key}"
    assert result.get("correlationId") == cid


def test_sync_extract_multipart_upload(session, token):
    cid = f"test-sync-upload-{uuid.uuid4().hex[:8]}"
    content = (
        "# Sample Invoice\n\n"
        "Invoice Number: INV-2026-001\n"
        "Date: 2026-01-15\n"
        "Customer: Acme Corp\n\n"
        "| Item | Qty | Price |\n|------|-----|-------|\n"
        "| Widget A | 2 | $10.00 |\n| Widget B | 1 | $25.00 |\n\n"
        "Total: $45.00\n"
    )
    files = {"file": ("sample.md", io.BytesIO(content.encode("utf-8")), "text/markdown")}
    data = {"instructions": "Extract invoice fields and totals."}
    r = session.post(
        f"{API}/extract/sync",
        files=files,
        data=data,
        headers={**_auth(token), "X-Correlation-Id": cid},
        timeout=SYNC_TIMEOUT,
    )
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    _assert_mock_result(result)


# ---------------------------------------------------------------------------
# ASYNC extraction
# ---------------------------------------------------------------------------
def test_async_extract_and_poll_completion(session, token):
    cid = f"test-async-{uuid.uuid4().hex[:8]}"
    r = session.post(
        f"{API}/extract/async",
        json={
            "s3Uri": "s3://demo-bucket/contracts/invoice-001.pdf",
            "instructions": "Extract structured invoice JSON.",
        },
        headers={**_auth(token), "X-Correlation-Id": cid},
        timeout=15,
    )
    assert r.status_code == 202, r.text
    accepted = r.json()
    job_id = accepted["jobId"]
    assert accepted["status"] == "PENDING"
    assert accepted["statusUrl"] == f"/api/v1/jobs/{job_id}"
    assert accepted["correlationId"] == cid

    # Poll until COMPLETED or FAILED
    final = _poll_until_terminal(session, token, job_id)

    assert final is not None, "No job response received"
    assert final["status"] == "COMPLETED", f"Job ended in unexpected state: {final}"
    assert final["result"] is not None
    assert isinstance(final["result"].get("structured"), dict)
    assert final["correlationId"] == cid


def test_get_job_nonexistent_returns_404(session, token):
    fake_id = "nonexistent" + uuid.uuid4().hex
    r = session.get(f"{API}/jobs/{fake_id}", headers=_auth(token), timeout=10)
    assert r.status_code == 404
    assert "application/problem+json" in r.headers.get("content-type", "")
