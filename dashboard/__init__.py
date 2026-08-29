"""Dashboard package for FastAPI and Jinja2 interface."""

from dashboard.app import create_dashboard_app, extract_incident_events, run_dashboard

__all__ = [
    "create_dashboard_app",
    "extract_incident_events",
    "run_dashboard",
]
