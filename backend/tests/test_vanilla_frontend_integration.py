"""
Single-container + vanilla-frontend integration regression.
Validates:
  - FastAPI serves both UI (/) and API (/api/*) on the same origin.
  - Auth token mint, /documents listing + content retrieval, sync S3 extract, async polling.
"""
import os
import time
import pytest
import requests

BASE = "https://omniparse-k8s-deploy.preview.emergentagent.com"
LOCAL = "http://localhost:8001"


# --- Static (single-container) ---
class TestSingleContainerStatic:
    @pytest.mark.parametrize("origin", [BASE, LOCAL])
    @pytest.mark.parametrize("path,ctype_part", [
        ("/", "text/html"),
        ("/app.js", "javascript"),
        ("/styles.css", "css"),
    ])
    def test_static_assets_served(self, origin, path, ctype_part):
        r = requests.get(origin + path, timeout=15)
        assert r.status_code == 200, f"{origin}{path} -> {r.status_code}"
        assert ctype_part in r.headers.get("content-type", "").lower(), \
            f"unexpected content-type: {r.headers.get('content-type')}"

    def test_index_has_testids_and_no_react_env(self):
        r = requests.get(BASE + "/", timeout=10)
        body = r.text
        for tid in ("nav-console", "nav-docs", "jwt-input", "extract-btn",
                    "health-badge", "ready-badge", "correlation-id"):
            assert f'data-testid="{tid}"' in body, f"missing testid {tid}"
        assert "REACT_APP_BACKEND_URL" not in body
        # No external CDN/fonts
        assert "fonts.googleapis.com" not in body
        assert "cdn.jsdelivr.net" not in body

    def test_app_js_uses_relative_urls(self):
        r = requests.get(BASE + "/app.js", timeout=10)
        assert r.status_code == 200
        body = r.text
        assert "REACT_APP_BACKEND_URL" not in body
        # Must reference the relative API base
        assert "/api/v1" in body


# --- Ops endpoints ---
class TestOpsEndpoints:
    @pytest.mark.parametrize("origin", [BASE, LOCAL])
    def test_health(self, origin):
        r = requests.get(origin + "/api/health", timeout=10)
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    def test_health_alias_direct_backend(self):
        # Single-container model: FastAPI exposes both /health and /api/health.
        # Preview ingress only proxies /api/*, so verify alias via direct backend port.
        r = requests.get(LOCAL + "/health", timeout=10)
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    def test_ready_via_api_prefix(self):
        # Vanilla UI calls /api/ready (single-origin same-prefix).
        r = requests.get(BASE + "/api/ready", timeout=10)
        assert r.status_code == 200
        assert r.json()["status"] == "ready"

    def test_ready_direct_backend(self):
        r = requests.get(LOCAL + "/ready", timeout=10)
        assert r.status_code == 200
        assert r.json()["status"] == "ready"


# --- Auth + Documents ---
@pytest.fixture(scope="module")
def token():
    r = requests.post(
        BASE + "/api/v1/auth/token",
        json={"sub": "demo-user", "roles": ["extractor", "admin"]},
        timeout=10,
    )
    assert r.status_code == 200
    tok = r.json()["accessToken"]
    assert isinstance(tok, str) and len(tok) > 100 and tok.count(".") == 2
    return tok


class TestAuthToken:
    def test_token_minted(self, token):
        assert len(token) > 100


class TestDocuments:
    def test_list(self):
        r = requests.get(BASE + "/api/v1/documents", timeout=10)
        assert r.status_code == 200
        ids = sorted(d["id"] for d in r.json())
        assert ids == ["app-flow", "benefits", "prd", "trd"]

    @pytest.mark.parametrize("doc_id", ["prd", "trd", "app-flow", "benefits"])
    def test_get_doc(self, doc_id):
        r = requests.get(BASE + f"/api/v1/documents/{doc_id}", timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == doc_id
        assert "#" in body["content"]
        assert len(body["content"]) > 200
        if doc_id == "benefits":
            # New iteration: BENEFITS.md is the token-economics doc.
            assert len(body["content"]) > 2000
            assert "token" in body["content"].lower()

    def test_unknown_doc_404(self):
        r = requests.get(BASE + "/api/v1/documents/__bogus__", timeout=10)
        assert r.status_code == 404


# --- Extraction (sync + async via S3 mock) ---
class TestExtraction:
    S3_URI = "s3://demo-bucket/contracts/invoice-001.pdf"

    def test_sync_s3(self, token):
        r = requests.post(
            BASE + "/api/v1/extract/sync",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Correlation-Id": "pytest-sync-1",
            },
            json={"s3Uri": self.S3_URI, "instructions": "Extract invoice fields."},
            timeout=120,
        )
        assert r.status_code == 200, r.text[:400]
        d = r.json()
        assert "result" in d
        # correlationId is nested inside result (per current API contract)
        assert d["result"].get("correlationId") == "pytest-sync-1"
        assert d["result"].get("structured") is not None
        # New iteration: token & chunking observability
        res = d["result"]
        assert isinstance(res.get("tokensEstimate"), int) and res["tokensEstimate"] > 0
        assert isinstance(res.get("tokensSavedVsRaw"), int) and res["tokensSavedVsRaw"] >= 0
        assert res.get("chunkCount") == 1
        assert res.get("chunked") is False

    def test_async_s3_completes(self, token):
        r = requests.post(
            BASE + "/api/v1/extract/async",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Correlation-Id": "pytest-async-1",
            },
            json={"s3Uri": self.S3_URI, "instructions": "Extract invoice fields."},
            timeout=30,
        )
        assert r.status_code in (200, 202), r.text[:400]
        job = r.json()
        job_id = job["jobId"]
        assert job["status"] in ("PENDING", "PROCESSING", "COMPLETED")

        deadline = time.time() + 60
        last = None
        while time.time() < deadline:
            poll = requests.get(
                BASE + f"/api/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            assert poll.status_code == 200, poll.text[:400]
            last = poll.json()
            if last["status"] in ("COMPLETED", "FAILED"):
                break
            time.sleep(1.5)
        assert last is not None and last["status"] == "COMPLETED", f"final={last}"
        assert last.get("result") is not None
        # New iteration: result carries token & chunking observability
        res = last["result"]
        assert isinstance(res.get("tokensEstimate"), int) and res["tokensEstimate"] > 0
        assert isinstance(res.get("tokensSavedVsRaw"), int) and res["tokensSavedVsRaw"] >= 0
        assert res.get("chunkCount") == 1


# --- Auth enforcement ---
class TestAuthEnforcement:
    def test_no_token_401(self):
        r = requests.post(
            BASE + "/api/v1/extract/sync",
            json={"s3Uri": TestExtraction.S3_URI},
            timeout=15,
        )
        assert r.status_code in (401, 403)
