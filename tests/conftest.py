"""Keep every test independent of the real cold-chain/.env.

config.py reads .env for any variable that is not already set, so passwords saved there would switch the login on in
tests that expect open access. An empty value counts as "set", so it wins over .env. tests/test_auth.py sets real codes itself.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_login_from_the_real_env_file(monkeypatch):
    for key in ("ADMIN_ID", "ADMIN_NAME", "ADMIN_CODE", "ADMIN_CONTACT", "DRIVER_ID", "DRIVER_NAME", "DRIVER_CODE",
                "DRIVER_CONTACT"):
        monkeypatch.setenv(f"COLDTRUST_{key}", "")
