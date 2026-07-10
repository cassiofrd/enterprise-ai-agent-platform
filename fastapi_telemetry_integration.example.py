"""Optional FastAPI bootstrap integration.

Add these two lines after creating the FastAPI app:

from shared.telemetry import instrument_fastapi_app, instrument_http_clients

instrument_fastapi_app(app)
instrument_http_clients()
"""
