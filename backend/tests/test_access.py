import base64
import getpass
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from aml import cli
from aml.control.tenants import Fleet, create_tenant
from aml.main import create_app
from aml.modules.access import security, service
from aml.modules.access.api import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from aml.modules.access.security import SecretCipher
from aml.modules.access.service import AccessError, AuthPolicy, SignInError
from aml.tenancy import slug_from_host
from tests.conftest import make_settings

pytestmark = pytest.mark.requirement("ACC-2")

PASSWORD = "correct horse battery staple"
EMAIL = "Ada.Officer@example.com"
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def secret_from_uri(uri: str) -> bytes:
    encoded = parse_qs(urlparse(uri).query)["secret"][0]
    return base64.b32decode(encoded + "=" * (-len(encoded) % 8))


def code_at(secret: bytes, at: datetime) -> str:
    return security.totp_code(secret, security.totp_step(at))


# --- Primitives ---------------------------------------------------------------------------


def test_totp_matches_rfc_6238_vector() -> None:
    # RFC 6238 appendix B, SHA-1, T = 59 s: 94287082 (8 digits) -> last 6 digits.
    assert security.totp_code(b"12345678901234567890", 59 // 30) == "287082"


def test_totp_allows_one_step_of_drift_and_refuses_replay() -> None:
    secret = security.new_totp_secret()
    step = security.totp_step(T0)
    earlier = security.totp_code(secret, step - 1)

    assert security.verify_totp(secret, earlier, T0, None) == step - 1
    assert security.verify_totp(secret, earlier, T0, step - 1) is None
    assert security.verify_totp(secret, security.totp_code(secret, step - 2), T0, None) is None


def test_passwords_are_argon2id_and_need_twelve_characters() -> None:
    hashed = security.hash_password(PASSWORD)
    assert hashed.startswith("$argon2id$")
    assert security.verify_password(hashed, PASSWORD)
    assert not security.verify_password(hashed, PASSWORD + "x")
    assert not security.verify_password(None, PASSWORD)
    with pytest.raises(ValueError, match="12 characters"):
        security.hash_password("short")


def test_csrf_token_is_bound_to_the_session() -> None:
    a, b = security.new_session_token(), security.new_session_token()
    assert security.csrf_token(a) == security.csrf_token(a) != security.csrf_token(b)


@pytest.mark.parametrize(
    ("host", "slug"),
    [
        ("lapo-mfb.aml.example:443", "lapo_mfb"),
        ("LAPO-MFB.aml.example", "lapo_mfb"),
        ("localhost:8000", "localhost"),
        ("1bad.aml.example", None),
        ("", None),
    ],
)
def test_slug_from_host(host: str, slug: str | None) -> None:
    assert slug_from_host(host) == slug


def test_sign_in_without_configuration_is_a_problem_response() -> None:
    client = TestClient(create_app(make_settings()))

    response = client.post("/v1/auth/sign-in", json={"email": "a", "password": "b", "code": "1"})

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "AUTH_NOT_CONFIGURED"
    assert body["request_id"] == response.headers["X-Request-Id"]


# --- Against a real tenant database --------------------------------------------------------


@dataclass
class Tenant:
    url: str
    host: str
    secret: bytes
    cipher: SecretCipher
    settings_overrides: dict[str, object]


@pytest.fixture
def tenant(fleet: Fleet, new_slug: Callable[[], str], tmp_path: Path) -> Tenant:
    created = create_tenant(fleet, new_slug(), "Auth Test MFB")
    key = Fernet.generate_key().decode()
    cipher = SecretCipher(key)
    url = fleet.secrets.get(created.slug)
    with psycopg.connect(url) as conn:
        _, uri = service.create_user(
            conn, cipher, email=EMAIL, full_name="Ada Officer", role="administrator",
            password=PASSWORD,
        )  # fmt: skip
    return Tenant(
        url=url,
        host=created.slug.replace("_", "-") + ".aml.test",
        secret=secret_from_uri(uri),
        cipher=cipher,
        settings_overrides={
            "database_url": fleet.control_url,
            "secrets_dir": tmp_path / "secrets",
            "mfa_key": key,
        },
    )


@pytest.fixture
def conn(tenant: Tenant) -> Iterator[psycopg.Connection]:
    with psycopg.connect(tenant.url) as connection:
        yield connection


@pytest.fixture
def client(tenant: Tenant) -> TestClient:
    app = create_app(make_settings(**tenant.settings_overrides))
    return TestClient(app, base_url=f"https://{tenant.host}")


def sign_in(conn: psycopg.Connection, tenant: Tenant, code: str, at: datetime) -> str:
    _, token = service.sign_in(
        conn, tenant.cipher, AuthPolicy(), email=EMAIL, password=PASSWORD, code=code, now=at
    )
    return token


def test_sign_in_me_and_sign_out_through_the_api(client: TestClient, tenant: Tenant) -> None:
    code = code_at(tenant.secret, datetime.now(UTC))
    response = client.post(
        "/v1/auth/sign-in", json={"email": EMAIL, "password": PASSWORD, "code": code}
    )

    assert response.status_code == 200
    assert response.json()["email"] == EMAIL.lower()
    cookies = {c.split("=", 1)[0]: c.lower() for c in response.headers.get_list("set-cookie")}
    for flag in ("secure", "samesite=strict", "path=/"):
        assert flag in cookies[SESSION_COOKIE]
        assert flag in cookies[CSRF_COOKIE]
    assert "httponly" in cookies[SESSION_COOKIE]
    assert "httponly" not in cookies[CSRF_COOKIE]

    assert client.get("/v1/auth/me").json()["role"] == "administrator"
    refused = client.post("/v1/auth/sign-out")
    assert refused.status_code == 403
    assert refused.json()["code"] == "CSRF_FAILED"

    csrf = client.cookies[CSRF_COOKIE]
    assert client.post("/v1/auth/sign-out", headers={CSRF_HEADER: csrf}).status_code == 204
    client.cookies.clear()
    assert client.get("/v1/auth/me").status_code == 401


def test_sign_in_fails_without_the_second_factor(client: TestClient) -> None:
    missing = client.post("/v1/auth/sign-in", json={"email": EMAIL, "password": PASSWORD})
    assert missing.status_code == 400
    assert PASSWORD not in missing.text

    wrong = client.post(
        "/v1/auth/sign-in", json={"email": EMAIL, "password": PASSWORD, "code": "000000"}
    )
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "SIGN_IN_FAILED"
    assert SESSION_COOKIE not in wrong.cookies


def test_unknown_institution_host_is_not_found(tenant: Tenant) -> None:
    app = create_app(make_settings(**tenant.settings_overrides))
    client = TestClient(app, base_url="https://no-such-bank.aml.test")

    response = client.get("/v1/auth/me")

    assert response.status_code == 404
    assert response.json()["code"] == "TENANT_UNKNOWN"


def test_repeated_failures_lock_the_account(conn: psycopg.Connection, tenant: Tenant) -> None:
    policy = AuthPolicy()
    for i in range(policy.max_failed_logins):
        with pytest.raises(SignInError):
            service.sign_in(
                conn, tenant.cipher, policy, email=EMAIL, password=PASSWORD, code="000000",
                now=T0 + timedelta(seconds=i),
            )  # fmt: skip

    locked_at = T0 + timedelta(minutes=1)
    with pytest.raises(SignInError):
        sign_in(conn, tenant, code_at(tenant.secret, locked_at), locked_at)

    after = T0 + policy.lockout + timedelta(minutes=1)
    assert sign_in(conn, tenant, code_at(tenant.secret, after), after)


def test_wrong_password_counts_and_success_resets_the_count(
    conn: psycopg.Connection, tenant: Tenant
) -> None:
    with pytest.raises(SignInError):
        service.sign_in(
            conn, tenant.cipher, AuthPolicy(), email=EMAIL, password="wrong password!",
            code=code_at(tenant.secret, T0), now=T0,
        )  # fmt: skip
    failed = conn.execute("SELECT failed_logins FROM app_user").fetchone()
    assert failed == (1,)

    sign_in(conn, tenant, code_at(tenant.secret, T0), T0)
    assert conn.execute("SELECT failed_logins FROM app_user").fetchone() == (0,)


def test_unknown_email_fails_like_a_wrong_password(
    conn: psycopg.Connection, tenant: Tenant
) -> None:
    with pytest.raises(SignInError):
        service.sign_in(
            conn, tenant.cipher, AuthPolicy(), email="nobody@example.com", password=PASSWORD,
            code=code_at(tenant.secret, T0), now=T0,
        )  # fmt: skip


def test_a_code_works_only_once(conn: psycopg.Connection, tenant: Tenant) -> None:
    code = code_at(tenant.secret, T0)
    sign_in(conn, tenant, code, T0)
    with pytest.raises(SignInError):
        sign_in(conn, tenant, code, T0 + timedelta(seconds=5))


def test_sessions_expire_when_idle_or_too_old(conn: psycopg.Connection, tenant: Tenant) -> None:
    policy = AuthPolicy()
    token = sign_in(conn, tenant, code_at(tenant.secret, T0), T0)

    # Used every 20 minutes, the session stays alive until its absolute limit.
    at = T0
    while at + timedelta(minutes=20) < T0 + policy.absolute:
        at += timedelta(minutes=20)
        assert service.authenticate(conn, policy, token, at) is not None
    assert service.authenticate(conn, policy, token, T0 + policy.absolute) is None

    later = T0 + timedelta(minutes=1)
    idle = sign_in(conn, tenant, code_at(tenant.secret, later), later)
    assert service.authenticate(conn, policy, idle, later + policy.idle) is None


def test_revoking_ends_every_session_at_once(conn: psycopg.Connection, tenant: Tenant) -> None:
    first = sign_in(conn, tenant, code_at(tenant.secret, T0), T0)
    t1 = T0 + timedelta(minutes=1)
    second = sign_in(conn, tenant, code_at(tenant.secret, t1), t1)
    user = service.authenticate(conn, AuthPolicy(), first, t1)
    assert user is not None

    assert service.revoke_sessions(conn, user.id, t1) == 2
    for token in (first, second):
        assert service.authenticate(conn, AuthPolicy(), token, t1) is None


def test_secrets_are_not_stored_in_clear(conn: psycopg.Connection, tenant: Tenant) -> None:
    token = sign_in(conn, tenant, code_at(tenant.secret, T0), T0)

    stored_hash = conn.execute("SELECT token_hash FROM app_session").fetchone()
    assert stored_hash == (security.token_hash(token),)
    password_hash, mfa = conn.execute(
        "SELECT password_hash, mfa_secret_encrypted FROM app_user"
    ).fetchone()  # type: ignore[misc]
    assert PASSWORD not in password_hash
    assert tenant.secret not in bytes(mfa)


def test_create_user_rejects_duplicates_and_bad_input(
    conn: psycopg.Connection, tenant: Tenant
) -> None:
    def create(email: str, role: str, password: str) -> None:
        service.create_user(
            conn, tenant.cipher, email=email, full_name="X", role=role, password=password
        )

    with pytest.raises(AccessError, match="already exists"):
        create(EMAIL.upper(), "analyst", PASSWORD)
    with pytest.raises(AccessError, match="Unknown role"):
        create("new@example.com", "superuser", PASSWORD)
    with pytest.raises(AccessError, match="12 characters"):
        create("new@example.com", "analyst", "short")


def test_cli_creates_first_administrator(
    fleet: Fleet,
    tenant: Tenant,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    slug = tenant.host.split(".")[0].replace("-", "_")
    monkeypatch.setattr(cli, "Settings", lambda: make_settings(**tenant.settings_overrides))
    monkeypatch.setattr(cli, "build_fleet", lambda settings: fleet)
    monkeypatch.setattr(getpass, "getpass", lambda prompt: PASSWORD)

    code = cli.main(["user", "create", slug, "--email", "admin@example.com", "--name", "Admin"])

    assert code == 0
    assert "otpauth://totp/" in capsys.readouterr().out
    with psycopg.connect(tenant.url) as conn:
        row = conn.execute(
            "SELECT role::text, mfa_enabled FROM app_user WHERE email = 'admin@example.com'"
        ).fetchone()
    assert row == ("administrator", True)
