import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app.alexa_verification import _CERT_CACHE
from app.config import get_settings


@pytest.fixture
def rsa_test_key_and_cert():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "echo-api.amazonaws.com"),
        ]
    )
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("echo-api.amazonaws.com")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def test_alexa_signature_enforcement(
    client: TestClient,
    rsa_test_key_and_cert,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key, cert = rsa_test_key_and_cert
    cert_url = "https://s3.amazonaws.com/echo.api/echo-api-cert.pem"
    _CERT_CACHE[cert_url] = (cert, datetime.now(UTC).timestamp())

    # 1. Flag OFF (default): requests pass without signature headers
    monkeypatch.setenv("ALEXA_ENFORCE_SIGNATURE", "false")
    get_settings.cache_clear()

    res_off = client.post("/alexa", json={"request": {"type": "LaunchRequest"}})
    assert res_off.status_code == 200

    # 2. Flag ON: missing headers -> 403
    monkeypatch.setenv("ALEXA_ENFORCE_SIGNATURE", "true")
    get_settings.cache_clear()

    res_no_headers = client.post("/alexa", json={"request": {"type": "LaunchRequest"}})
    assert res_no_headers.status_code == 403

    # 3. Flag ON: bad timestamp (> 150s old) -> 403
    stale_payload = {
        "request": {
            "type": "LaunchRequest",
            "timestamp": (datetime.now(UTC) - timedelta(seconds=200)).isoformat(),
        }
    }
    raw_stale = json.dumps(stale_payload).encode("utf-8")
    sig_stale = base64.b64encode(key.sign(raw_stale, padding.PKCS1v15(), hashes.SHA1())).decode("ascii")  # noqa: S303

    res_stale = client.post(
        "/alexa",
        content=raw_stale,
        headers={
            "Content-Type": "application/json",
            "Signature": sig_stale,
            "SignatureCertChainUrl": cert_url,
        },
    )
    assert res_stale.status_code == 403

    # 4. Flag ON: valid signature and fresh timestamp -> 200
    valid_payload = {
        "request": {
            "type": "LaunchRequest",
            "timestamp": datetime.now(UTC).isoformat(),
        }
    }
    raw_valid = json.dumps(valid_payload).encode("utf-8")
    sig_valid = base64.b64encode(key.sign(raw_valid, padding.PKCS1v15(), hashes.SHA1())).decode("ascii")  # noqa: S303

    res_valid = client.post(
        "/alexa",
        content=raw_valid,
        headers={
            "Content-Type": "application/json",
            "Signature": sig_valid,
            "SignatureCertChainUrl": cert_url,
        },
    )
    assert res_valid.status_code == 200
    assert "Continuity Hub" in res_valid.json()["response"]["outputSpeech"]["text"]
