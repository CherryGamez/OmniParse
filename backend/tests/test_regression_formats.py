"""
Regression test suite for the BUG FIX:
- image-only / empty PDFs (and any doc that yields no extractable text)
  must return 422 application/problem+json (not 500) on SYNC.
- ASYNC must accept the request (202), then resolve to status=FAILED
  with a clear `error` field.

Also exercises the full MarkItDown extras matrix on SYNC:
PDF, DOCX, PPTX, XLSX, TXT, MD, HTML, CSV, JSON.
"""
import io
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api/v1"
SYNC_TIMEOUT = 60
ASYNC_POLL_TIMEOUT = 45


@pytest.fixture(scope="module")
def session():
    return requests.Session()


@pytest.fixture(scope="module")
def token(session):
    r = session.post(
        f"{API}/auth/token",
        json={"sub": "regression-user", "roles": ["extractor", "admin"]},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    return r.json()["accessToken"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# -------- Pre-staged files in /tmp (created by main agent) ----------
FORMAT_FILES = [
    ("text.pdf", "application/pdf"),
    ("s.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("s.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ("s.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("s.txt", "text/plain"),
    ("s.md", "text/markdown"),
    ("s.html", "text/html"),
    ("s.csv", "text/csv"),
    ("s.json", "application/json"),
]


def _upload(session, token, filename, mime, cid_prefix="reg"):
    path = f"/tmp/{filename}"
    if not os.path.exists(path):
        pytest.skip(f"Test file missing: {path}")
    with open(path, "rb") as fh:
        files = {"file": (filename, fh.read(), mime)}
    cid = f"{cid_prefix}-{uuid.uuid4().hex[:8]}"
    return session.post(
        f"{API}/extract/sync",
        files=files,
        data={"instructions": "Extract structured JSON."},
        headers={**_auth(token), "X-Correlation-Id": cid},
        timeout=SYNC_TIMEOUT,
    ), cid


@pytest.mark.parametrize("filename,mime", FORMAT_FILES)
def test_sync_format_matrix_returns_200_structured(session, token, filename, mime):
    r, cid = _upload(session, token, filename, mime)
    assert r.status_code == 200, f"{filename}: status={r.status_code} body={r.text[:300]}"
    assert r.headers.get("X-Correlation-Id") == cid
    payload = r.json()
    result = payload["result"]
    assert result.get("correlationId") == cid
    assert result.get("mock") is True
    assert result.get("model") == "mock"
    assert isinstance(result.get("markdown"), str) and len(result["markdown"]) > 0, \
        f"markdownChars=0 for {filename}"
    structured = result.get("structured") or {}
    for key in ("documentType", "title", "sections", "fields", "summary"):
        assert key in structured, f"{filename}: missing structured.{key}"


# ----------- THE BUG FIX: blank PDF returns 422, not 500 ----------
def test_sync_blank_pdf_returns_422_problem_json(session, token):
    path = "/tmp/blank.pdf"
    if not os.path.exists(path):
        pytest.skip("blank.pdf missing")
    cid = f"blank-{uuid.uuid4().hex[:8]}"
    with open(path, "rb") as fh:
        files = {"file": ("blank.pdf", fh.read(), "application/pdf")}
    r = session.post(
        f"{API}/extract/sync",
        files=files,
        data={"instructions": "extract"},
        headers={**_auth(token), "X-Correlation-Id": cid},
        timeout=SYNC_TIMEOUT,
    )
    assert r.status_code == 422, f"Expected 422 got {r.status_code} body={r.text[:300]}"
    assert "application/problem+json" in r.headers.get("content-type", "")
    body = r.json()
    detail = (body.get("detail") or "").lower()
    assert "no extractable text" in detail or "extractable" in detail, \
        f"Missing 'no extractable text' phrase in detail: {body}"
    assert body.get("correlationId") == cid
    assert r.headers.get("X-Correlation-Id") == cid


# ----------- ASYNC blank PDF -> 202 then status=FAILED ----------
def test_async_blank_pdf_polls_to_failed(session, token):
    path = "/tmp/blank.pdf"
    if not os.path.exists(path):
        pytest.skip("blank.pdf missing")
    cid = f"async-blank-{uuid.uuid4().hex[:8]}"
    with open(path, "rb") as fh:
        files = {"file": ("blank.pdf", fh.read(), "application/pdf")}
    r = session.post(
        f"{API}/extract/async",
        files=files,
        data={"instructions": "extract"},
        headers={**_auth(token), "X-Correlation-Id": cid},
        timeout=15,
    )
    assert r.status_code == 202, r.text
    accepted = r.json()
    job_id = accepted["jobId"]
    assert accepted["status"] == "PENDING"

    deadline = time.time() + ASYNC_POLL_TIMEOUT
    final = None
    while time.time() < deadline:
        jr = session.get(f"{API}/jobs/{job_id}", headers=_auth(token), timeout=15)
        assert jr.status_code == 200, jr.text
        final = jr.json()
        if final["status"] in ("COMPLETED", "FAILED"):
            break
        time.sleep(1.5)

    assert final is not None
    assert final["status"] == "FAILED", f"Expected FAILED got {final}"
    err = (final.get("error") or "").lower()
    assert "extractable" in err or "no extractable text" in err, \
        f"Missing clear error message: {final}"


# ----------- S3 sync returns 200 ----------
def test_sync_s3_json_body_returns_200(session, token):
    cid = f"s3-{uuid.uuid4().hex[:8]}"
    r = session.post(
        f"{API}/extract/sync",
        json={"s3Uri": "s3://demo-bucket/contracts/invoice-001.pdf"},
        headers={**_auth(token), "X-Correlation-Id": cid},
        timeout=SYNC_TIMEOUT,
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("X-Correlation-Id") == cid
    payload = r.json()
    # correlationId lives under result for sync responses
    assert payload["result"].get("correlationId") == cid
    assert payload["result"]["mock"] is True


# ============================================================
# OFFLINE OCR (PaddleOCR PP-OCRv5, multilingual latin: de+en) regression
# ============================================================
def _upload_path(session, token, fs_path, filename, mime, cid_prefix="ocr"):
    if not os.path.exists(fs_path):
        pytest.skip(f"Test file missing: {fs_path}")
    with open(fs_path, "rb") as fh:
        files = {"file": (filename, fh.read(), mime)}
    cid = f"{cid_prefix}-{uuid.uuid4().hex[:8]}"
    return session.post(
        f"{API}/extract/sync",
        files=files,
        data={"instructions": "Extract structured JSON."},
        headers={**_auth(token), "X-Correlation-Id": cid},
        timeout=SYNC_TIMEOUT + 30,
    ), cid


def test_sync_ocr_image_jpeg_german(session, token):
    """OCR on a JPEG image with German text -> ocrUsed=true, ocrEngine=paddleocr:latin."""
    r, cid = _upload_path(session, token, "/tmp/german_doc.jpg", "german_doc.jpg",
                          "image/jpeg", cid_prefix="ocr-img")
    assert r.status_code == 200, f"status={r.status_code} body={r.text[:400]}"
    assert r.headers.get("X-Correlation-Id") == cid
    payload = r.json()
    result = payload["result"]
    assert result.get("correlationId") == cid
    assert result.get("mock") is True
    assert result.get("model") == "mock"
    assert result.get("ocrUsed") is True, f"ocrUsed must be True for image, got {result.get('ocrUsed')}"
    engine = (result.get("ocrEngine") or "").lower()
    assert "paddleocr" in engine, f"Expected paddleocr:latin, got '{engine}'"
    md = result.get("markdown") or ""
    assert len(md) > 0, "markdownChars=0 from OCR image"
    structured = result.get("structured") or {}
    for key in ("documentType", "title", "sections", "fields", "summary"):
        assert key in structured, f"missing structured.{key}"


def test_sync_ocr_personalausweis_detection(session, token):
    """Personalausweis-style image -> documentType=='Personalausweis' with surname/givenNames/dateOfBirth."""
    r, cid = _upload_path(session, token, "/tmp/ausweis.png", "ausweis.png",
                          "image/png", cid_prefix="ausweis")
    assert r.status_code == 200, f"status={r.status_code} body={r.text[:400]}"
    payload = r.json()
    result = payload["result"]
    assert result.get("ocrUsed") is True
    structured = result.get("structured") or {}
    assert structured.get("documentType") == "Personalausweis", \
        f"Expected Personalausweis, got {structured.get('documentType')}"
    # surname / givenNames / dateOfBirth populated
    surname = structured.get("surname")
    given = structured.get("givenNames")
    dob = structured.get("dateOfBirth")
    assert surname, f"surname missing in {structured}"
    assert given, f"givenNames missing in {structured}"
    assert dob, f"dateOfBirth missing in {structured}"


def test_sync_scanned_pdf_ocr_fallback(session, token):
    """Image-only PDF (no text layer) -> OCR fallback path: 200, ocrUsed=true, markdownChars>0."""
    r, cid = _upload_path(session, token, "/tmp/scanned.pdf", "scanned.pdf",
                          "application/pdf", cid_prefix="scan")
    assert r.status_code == 200, f"status={r.status_code} body={r.text[:400]}"
    payload = r.json()
    result = payload["result"]
    assert result.get("ocrUsed") is True, f"OCR fallback must trigger for image-only PDF; got {result}"
    assert len((result.get("markdown") or "")) > 0


def test_sync_german_chars_preserved(session, token):
    """ä ö ü ß survive into response (may be \\uXXXX-escaped in transport but decode correctly)."""
    content = "Begrüßung: Ärger, Öl, Übung — schöner Tag mit ß und Umlauten ä ö ü.".encode("utf-8")
    cid = f"de-{uuid.uuid4().hex[:8]}"
    files = {"file": ("umlauts.md", content, "text/markdown")}
    r = session.post(
        f"{API}/extract/sync",
        files=files,
        data={"instructions": "extract"},
        headers={**_auth(token), "X-Correlation-Id": cid},
        timeout=SYNC_TIMEOUT,
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    md = payload["result"].get("markdown") or ""
    # decoded JSON in python; chars must round-trip
    for ch in ["ä", "ö", "ü", "ß"]:
        assert ch in md, f"German char '{ch}' not preserved in markdown: {md[:200]}"


def test_async_image_ocr_completes(session, token):
    """ASYNC upload an image -> 202 PENDING -> COMPLETED with result.ocrUsed=true."""
    path = "/tmp/german_doc.jpg"
    if not os.path.exists(path):
        pytest.skip("german_doc.jpg missing")
    cid = f"async-ocr-{uuid.uuid4().hex[:8]}"
    with open(path, "rb") as fh:
        files = {"file": ("german_doc.jpg", fh.read(), "image/jpeg")}
    r = session.post(
        f"{API}/extract/async",
        files=files,
        data={"instructions": "extract"},
        headers={**_auth(token), "X-Correlation-Id": cid},
        timeout=15,
    )
    assert r.status_code == 202, r.text
    accepted = r.json()
    job_id = accepted["jobId"]
    assert accepted["status"] == "PENDING"

    deadline = time.time() + ASYNC_POLL_TIMEOUT + 30
    final = None
    while time.time() < deadline:
        jr = session.get(f"{API}/jobs/{job_id}", headers=_auth(token), timeout=15)
        assert jr.status_code == 200, jr.text
        final = jr.json()
        if final["status"] in ("COMPLETED", "FAILED"):
            break
        time.sleep(1.5)

    assert final and final["status"] == "COMPLETED", f"Expected COMPLETED got {final}"
    res = final.get("result") or {}
    assert res.get("ocrUsed") is True

