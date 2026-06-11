"""
Supervisor entrypoint shim.

The platform's process manager runs ``uvicorn server:app``. The real application
lives in ``main.py`` (per the requested project layout), so we simply re-export
it here.
"""
from main import app  # noqa: F401
