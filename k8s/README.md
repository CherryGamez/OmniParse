# Kubernetes manifests

Minimal, opinionated single-container deployment of the Document Intelligence
Platform. The FastAPI container serves **both** the REST API (`/api/*`) and the
vanilla HTML console (`/`) — no separate frontend pod.

## Files

| File              | What it does                                                    |
|-------------------|-----------------------------------------------------------------|
| `deployment.yaml` | One Deployment, one container, `/health` + `/ready` probes      |
| `service.yaml`    | ClusterIP service exposing port `80` → pod `8001`               |

## Quick start

```bash
# 1. Build & push an image that contains both backend/ and frontend/dist/
docker build -t YOUR_REGISTRY/doc-intel:1.0.0 .
docker push YOUR_REGISTRY/doc-intel:1.0.0

# 2. Set the image in deployment.yaml ("image: doc-intel:latest")

# 3. Apply
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

# 4. Expose
#    (a) port-forward for a quick smoke test:
kubectl port-forward svc/doc-intel 8080:80
# → http://localhost:8080
#    (b) or add your existing Ingress / Service Mesh route to svc/doc-intel.
```

## Air-gapped configuration

The default env in `deployment.yaml` points at an in-cluster
OpenAI-compatible LLM gateway:

```yaml
- name: LLM_PROVIDER
  value: openai_compatible
- name: OPENAI_BASE_URL
  value: http://llm-gateway.internal/v1
- name: OPENAI_MODEL
  value: llama3.1
```

Adjust the URL / model to whatever runs in your cluster
(vLLM / Ollama / TGI / internal LiteLLM gateway). No outbound internet required.

## Notes

- For HA / multi-replica, swap the embedded SQLite for PostgreSQL:
  set `DATABASE_URL=postgresql+asyncpg://USER:PASS@host:5432/dbname` (and
  `pip install asyncpg` at image-build time).
- For HEIC / scanned-PDF / German-ID OCR, install `tesseract-ocr`,
  `tesseract-ocr-deu`, `tesseract-ocr-eng` in your container image.
