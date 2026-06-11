"""
Backend tests for the vision-LLM extraction path, AVIF handling, /documents
endpoints, blank-doc regression and auth/RBAC.

These tests assume the LIVE config (LLM_PROVIDER=emergent, USE_MOCK_LLM=false,
OCR_VISION=true) — they will trigger real LLM calls (5-15s each).
"""
import io
import os
import time

import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api/v1"

FIX_DRIV = "/app/tests/fixtures/drivlicense.jpg"
FIX_AVIF = "/app/tests/fixtures/fuehrerschein.avif"

SYNC_TIMEOUT = 120
ASYNC_POLL_TIMEOUT = 150


# -------- fixtures --------------------------------------------------------
@pytest.fixture(scope="session")
def session():
    return requests.Session()


@pytest.fixture(scope="session")
def token(session):
    r = session.post(
        f"{API}/auth/token",
        json={"sub": "tester", "roles": ["extractor", "admin"]},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    return r.json()["accessToken"]


@pytest.fixture(scope="session")
def viewer_token(session):
    r = session.post(
        f"{API}/auth/token",
        json={"sub": "viewer", "roles": ["viewer"]},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    return r.json()["accessToken"]


@pytest.fixture
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# -------- documents endpoints ---------------------------------------------
class TestDocuments:
    def test_list_documents_returns_three(self, session):
        r = session.get(f"{API}/documents", timeout=15)
        assert r.status_code == 200
        data = r.json()
        ids = {d["id"] for d in data}
        assert {"prd", "trd", "app-flow"}.issubset(ids)
        assert len(data) == 3

    def test_get_document_prd(self, session):
        r = session.get(f"{API}/documents/prd", timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "prd"
        assert "content" in body and isinstance(body["content"], str)
        assert len(body["content"]) > 50
        # Markdown sanity
        assert "#" in body["content"]

    def test_get_document_trd(self, session):
        r = session.get(f"{API}/documents/trd", timeout=15)
        assert r.status_code == 200
        assert "content" in r.json()

    def test_get_document_app_flow(self, session):
        r = session.get(f"{API}/documents/app-flow", timeout=15)
        assert r.status_code == 200
        assert "content" in r.json()

    def test_get_unknown_document_404(self, session):
        r = session.get(f"{API}/documents/unknown", timeout=15)
        assert r.status_code == 404


# -------- auth / RBAC ------------------------------------------------------
class TestAuthEnforcement:
    def test_sync_without_token_401(self, session):
        r = session.post(
            f"{API}/extract/sync",
            json={"s3Uri": "s3://demo-bucket/contracts/invoice-001.pdf"},
            timeout=15,
        )
        assert r.status_code == 401
        # RFC 7807 problem+json
        ct = r.headers.get("content-type", "")
        assert "json" in ct

    def test_sync_viewer_role_403(self, session, viewer_token):
        r = session.post(
            f"{API}/extract/sync",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={"s3Uri": "s3://demo-bucket/contracts/invoice-001.pdf"},
            timeout=15,
        )
        assert r.status_code == 403


# -------- vision LLM extraction -------------------------------------------
class TestVisionExtraction:
    def test_personalausweis_vision_extraction(self, session, auth_headers):
        with open(FIX_DRIV, "rb") as f:
            files = {"file": ("drivlicense.jpg", f, "image/jpeg")}
            data = {"instructions": "Extract the ID card fields"}
            r = session.post(
                f"{API}/extract/sync",
                headers=auth_headers,
                files=files,
                data=data,
                timeout=SYNC_TIMEOUT,
            )
        assert r.status_code == 200, r.text
        body = r.json()
        result = body["result"]
        # mock must be false in real-LLM config
        assert result.get("mock") is False, f"Expected mock=False; got result={result}"
        # ocrUsed should be true and engine starts with "vision:"
        assert result.get("ocrUsed") is True
        engine = result.get("ocrEngine") or ""
        assert engine.startswith("vision:"), f"Unexpected ocrEngine={engine!r}"

        structured = result.get("structured") or {}
        # Compare case-insensitive across nested too
        flat = str(structured).upper()
        assert "MUSTERMANN" in flat, f"surname MUSTERMANN missing; structured={structured}"
        assert "HANS" in flat, f"givenNames HANS missing; structured={structured}"
        # date of birth / expiry – tolerate dot/dash separators
        # 14.03.1967 and 10.12.2028
        assert ("14.03.1967" in flat) or ("14-03-1967" in flat) or ("1967-03-14" in flat), (
            f"DOB 14.03.1967 missing; structured={structured}"
        )
        assert ("10.12.2028" in flat) or ("10-12-2028" in flat) or ("2028-12-10" in flat), (
            f"dateOfExpiry 10.12.2028 missing; structured={structured}"
        )

    def test_fuehrerschein_avif_vision_extraction(self, session, auth_headers):
        with open(FIX_AVIF, "rb") as f:
            files = {"file": ("fuehrerschein.avif", f, "image/avif")}
            data = {"instructions": "Extract driving licence fields including categories"}
            r = session.post(
                f"{API}/extract/sync",
                headers=auth_headers,
                files=files,
                data=data,
                timeout=SYNC_TIMEOUT,
            )
        assert r.status_code == 200, r.text
        body = r.json()
        result = body["result"]
        assert result.get("mock") is False
        # documentType should hint at driving licence
        doc_type = (result.get("documentType") or "").lower()
        structured = result.get("structured") or {}
        flat_lower = str(structured).lower()
        assert any(
            tok in (doc_type + " " + flat_lower)
            for tok in ["fuhrerschein", "führerschein", "driving", "licen", "driver"]
        ), f"Expected licence indicator; documentType={doc_type!r}, structured={structured}"

        # categories may live under multiple keys -- search flattened structured for letters
        # Look for at least one of A, B, C, D, BE, etc. as a "category" indicator
        cat_field = None
        for k in ("categories", "classes", "licenceCategories", "vehicleCategories"):
            if k in structured:
                cat_field = structured[k]
                break
        # Either explicit field, or in flattened text
        if cat_field is None:
            assert any(
                f'"{c}"' in flat_lower or f"'{c.lower()}'" in flat_lower
                for c in ["a", "b", "c"]
            ), f"categories not found in structured={structured}"
        else:
            assert isinstance(cat_field, (list, str, dict)), cat_field

    def test_s3_invoice_real_llm(self, session, auth_headers):
        r = session.post(
            f"{API}/extract/sync",
            headers=auth_headers,
            json={"s3Uri": "s3://demo-bucket/contracts/invoice-001.pdf"},
            timeout=SYNC_TIMEOUT,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        result = body["result"]
        assert result.get("mock") is False, f"Expected mock=False; got {result}"
        structured = result.get("structured") or {}
        assert isinstance(structured, dict) and len(structured) > 0


# -------- async path -------------------------------------------------------
class TestAsyncImage:
    def test_async_image_completes(self, session, auth_headers):
        with open(FIX_DRIV, "rb") as f:
            files = {"file": ("drivlicense.jpg", f, "image/jpeg")}
            data = {"instructions": "Extract ID fields"}
            r = session.post(
                f"{API}/extract/async",
                headers=auth_headers,
                files=files,
                data=data,
                timeout=30,
            )
        assert r.status_code == 202, r.text
        body = r.json()
        job_id = body["jobId"]
        assert body["status"] == "PENDING"

        deadline = time.time() + ASYNC_POLL_TIMEOUT
        final_status = None
        final_body = None
        while time.time() < deadline:
            jr = session.get(f"{API}/jobs/{job_id}", headers=auth_headers, timeout=15)
            assert jr.status_code == 200
            jb = jr.json()
            final_status = jb["status"]
            final_body = jb
            if final_status in ("COMPLETED", "FAILED"):
                break
            time.sleep(2)
        assert final_status == "COMPLETED", f"job final_status={final_status} body={final_body}"
        assert final_body["result"]["ocrUsed"] is True


# -------- blank/corrupt regression ----------------------------------------
class TestBlankRegression:
    def test_blank_pdf_returns_422(self, session, auth_headers, tmp_path):
        # 1-byte PDF: deliberately corrupt
        p = tmp_path / "blank.pdf"
        p.write_bytes(b"%")
        with open(p, "rb") as f:
            files = {"file": ("blank.pdf", f, "application/pdf")}
            r = session.post(
                f"{API}/extract/sync",
                headers=auth_headers,
                files=files,
                data={"instructions": "x"},
                timeout=60,
            )
        # Acceptable: 422 problem+json. Anything else (500) is a regression.
        assert r.status_code == 422, f"Expected 422 for blank/corrupt PDF, got {r.status_code}: {r.text[:400]}"
        body = r.json()
        # problem+json structure: detail/title contains "no extractable text" or similar
        msg = str(body).lower()
        assert "extract" in msg or "text" in msg or "no readable" in msg
