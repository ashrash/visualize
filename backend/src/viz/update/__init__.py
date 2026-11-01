"""Self-update: checks the project's GitHub Releases for a newer version and, for an install
made with `uv tool install`, can reinstall from the latest release's wheel. See check.py (the
GitHub poll, cached) and apply.py (the in-place reinstall and its status)."""
