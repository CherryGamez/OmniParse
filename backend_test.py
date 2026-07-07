#!/usr/bin/env python3
"""
Backend API test suite for OmniParse after PaddleOCR (PP-OCRv5) integration.

Tests:
1. Health and readiness endpoints
2. Auth token minting (mock JWT)
3. Regression test: non-OCR document extraction
4. OCR test: German+English image-only PDF with PaddleOCR
"""
import io
import json
import sys
import requests

# Backend URL - using localhost:8001 as backend runs internally on this port
BASE_URL = "http://localhost:8001"

def test_health():
    """Test GET /api/health endpoint."""
    print("\n=== TEST 1: Health Check ===")
    try:
        response = requests.get(f"{BASE_URL}/api/health", timeout=10)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data.get("status") == "healthy", f"Expected status 'healthy', got {data.get('status')}"
        print("✅ Health check PASSED")
        return True
    except Exception as e:
        print(f"❌ Health check FAILED: {e}")
        return False

def test_ready():
    """Test GET /api/ready endpoint."""
    print("\n=== TEST 2: Readiness Check ===")
    try:
        response = requests.get(f"{BASE_URL}/api/ready", timeout=10)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data.get("status") in ["ready", "not-ready"], f"Unexpected status: {data.get('status')}"
        print("✅ Readiness check PASSED")
        return True
    except Exception as e:
        print(f"❌ Readiness check FAILED: {e}")
        return False

def mint_token():
    """Mint a mock JWT token for authentication."""
    print("\n=== TEST 3: Auth Token Minting ===")
    try:
        response = requests.post(
            f"{BASE_URL}/api/v1/auth/token",
            json={"sub": "tester", "roles": ["extractor", "admin"]},
            timeout=10
        )
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "accessToken" in data, "No accessToken in response"
        token = data["accessToken"]
        print(f"✅ Token minted successfully: {token[:50]}...")
        return token
    except Exception as e:
        print(f"❌ Token minting FAILED: {e}")
        return None

def test_regression_non_ocr(token):
    """Test extraction with a simple text document (non-OCR path)."""
    print("\n=== TEST 4: Regression Test (Non-OCR Document) ===")
    if not token:
        print("❌ Skipping - no auth token")
        return False
    
    try:
        # Create a simple CSV file for testing
        csv_content = """Name,Age,City
John Doe,30,New York
Jane Smith,25,Los Angeles
Bob Johnson,35,Chicago"""
        
        files = {
            'file': ('test_data.csv', io.BytesIO(csv_content.encode('utf-8')), 'text/csv')
        }
        headers = {
            'Authorization': f'Bearer {token}'
        }
        
        response = requests.post(
            f"{BASE_URL}/api/v1/extract/sync",
            files=files,
            headers=headers,
            timeout=60
        )
        
        print(f"Status Code: {response.status_code}")
        
        if response.status_code != 200:
            print(f"Response: {response.text}")
            assert False, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        print(f"Response keys: {list(data.keys())}")
        
        # Check for required fields
        assert "result" in data, "No 'result' field in response"
        result = data["result"]
        
        assert "markdown" in result, "No 'markdown' field in result"
        assert "structured" in result, "No 'structured' field in result"
        
        print(f"Markdown length: {len(result['markdown'])} chars")
        print(f"Structured data: {json.dumps(result.get('structured'), indent=2)[:200]}...")
        
        # Verify the pipeline still works
        assert len(result['markdown']) > 0, "Markdown is empty"
        print("✅ Regression test PASSED - pipeline works after OCR swap")
        return True
        
    except Exception as e:
        print(f"❌ Regression test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_ocr_german_english_pdf(token):
    """Test OCR extraction with German+English image-only PDF."""
    print("\n=== TEST 5: OCR Test (German+English PDF with PaddleOCR) ===")
    if not token:
        print("❌ Skipping - no auth token")
        return False
    
    try:
        pdf_path = "/app/de_scan.pdf"
        
        with open(pdf_path, 'rb') as f:
            files = {
                'file': ('de_scan.pdf', f, 'application/pdf')
            }
            headers = {
                'Authorization': f'Bearer {token}'
            }
            
            print(f"Uploading {pdf_path}...")
            response = requests.post(
                f"{BASE_URL}/api/v1/extract/sync",
                files=files,
                headers=headers,
                timeout=120  # OCR can take longer
            )
        
        print(f"Status Code: {response.status_code}")
        
        if response.status_code != 200:
            print(f"Response: {response.text}")
            assert False, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        print(f"Response keys: {list(data.keys())}")
        
        # Check for required fields
        assert "result" in data, "No 'result' field in response"
        result = data["result"]
        
        # Critical checks for OCR
        print(f"\n--- OCR Verification ---")
        print(f"ocrUsed: {result.get('ocrUsed')}")
        print(f"ocrEngine: {result.get('ocrEngine')}")
        
        assert result.get("ocrUsed") == True, f"Expected ocrUsed=true, got {result.get('ocrUsed')}"
        assert result.get("ocrEngine") == "paddleocr:latin", f"Expected ocrEngine='paddleocr:latin', got '{result.get('ocrEngine')}'"
        
        # Check markdown content
        markdown = result.get("markdown", "")
        print(f"\nMarkdown length: {len(markdown)} chars")
        print(f"Markdown preview (first 500 chars):\n{markdown[:500]}")
        
        # Verify German text with umlauts is present
        german_words = ["München", "Straße", "schön", "Müller", "Über", "Äpfel", "ü", "ö", "ä", "ß"]
        found_german = []
        for word in german_words:
            if word.lower() in markdown.lower():
                found_german.append(word)
        
        print(f"\nGerman words/chars found: {found_german}")
        
        # Check for English text
        english_indicators = ["english", "2025", "ok", "text"]
        found_english = []
        for word in english_indicators:
            if word.lower() in markdown.lower():
                found_english.append(word)
        
        print(f"English words found: {found_english}")
        
        # Assertions
        assert len(markdown) > 0, "Markdown is empty"
        assert len(found_german) > 0, f"No German text with umlauts found in markdown. Expected words like: {german_words}"
        assert len(found_english) > 0, f"No English text found in markdown. Expected words like: {english_indicators}"
        
        print("\n✅ OCR test PASSED - PaddleOCR correctly extracted German+English text")
        return True
        
    except Exception as e:
        print(f"❌ OCR test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

def verify_no_tesseract():
    """Verify there's no tesseract dependency."""
    print("\n=== TEST 6: Verify No Tesseract Dependency ===")
    try:
        # Check if pytesseract is importable (it shouldn't be)
        try:
            import pytesseract
            print("⚠️  WARNING: pytesseract is still importable!")
            return False
        except ImportError:
            print("✅ pytesseract is not installed (expected)")
        
        # Check requirements.txt
        with open("/app/backend/requirements.txt", "r") as f:
            requirements = f.read().lower()
            if "tesseract" in requirements or "pytesseract" in requirements:
                print("❌ FAILED: tesseract/pytesseract found in requirements.txt")
                return False
            else:
                print("✅ No tesseract/pytesseract in requirements.txt")
        
        # Check if rapidocr-onnxruntime is present
        if "rapidocr" in requirements:
            print("✅ rapidocr-onnxruntime found in requirements.txt")
        else:
            print("⚠️  WARNING: rapidocr-onnxruntime not found in requirements.txt")
        
        print("✅ Tesseract dependency verification PASSED")
        return True
        
    except Exception as e:
        print(f"❌ Tesseract verification FAILED: {e}")
        return False

def main():
    """Run all tests."""
    print("=" * 70)
    print("OmniParse Backend Test Suite - PaddleOCR Integration")
    print("=" * 70)
    
    results = {}
    
    # Test 1: Health
    results["health"] = test_health()
    
    # Test 2: Ready
    results["ready"] = test_ready()
    
    # Test 3: Auth
    token = mint_token()
    results["auth"] = token is not None
    
    # Test 4: Regression (non-OCR)
    results["regression"] = test_regression_non_ocr(token)
    
    # Test 5: OCR (German+English PDF)
    results["ocr"] = test_ocr_german_english_pdf(token)
    
    # Test 6: No Tesseract
    results["no_tesseract"] = verify_no_tesseract()
    
    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    for test_name, passed in results.items():
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{test_name.upper()}: {status}")
    
    total = len(results)
    passed = sum(results.values())
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests PASSED!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) FAILED")
        return 1

if __name__ == "__main__":
    sys.exit(main())
