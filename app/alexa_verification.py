import base64
import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509.oid import ExtensionOID
from fastapi import HTTPException, Request, status

from app.config import get_settings

logger = logging.getLogger(__name__)

# Cache: cert_url -> (cert_object, timestamp)
_CERT_CACHE: dict[str, tuple[x509.Certificate, float]] = {}


def validate_cert_url(cert_url: str) -> None:
    """Validate that the certificate URL conforms to Amazon Alexa requirements."""
    parsed = urllib.parse.urlparse(cert_url)
    if parsed.scheme.lower() != "https":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SignatureCertChainUrl must use HTTPS",
        )

    host = parsed.hostname.lower() if parsed.hostname else ""
    if host != "s3.amazonaws.com":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SignatureCertChainUrl host must be s3.amazonaws.com",
        )

    if parsed.port and parsed.port != 443:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SignatureCertChainUrl port must be 443",
        )

    normalized_path = urllib.parse.unquote(parsed.path)
    if not normalized_path.startswith("/echo.api/"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SignatureCertChainUrl path must start with /echo.api/",
        )


def get_certificate(cert_url: str) -> x509.Certificate:
    """Download and cache the X.509 certificate for up to 1 hour."""
    now = time.time()
    if cert_url in _CERT_CACHE:
        cert, cached_time = _CERT_CACHE[cert_url]
        if now - cached_time < 3600:
            return cert

    validate_cert_url(cert_url)

    try:
        req = urllib.request.Request(cert_url, headers={"User-Agent": "Alexa-Hub-Verifier/1.0"})  # noqa: S310
        with urllib.request.urlopen(req, timeout=5.0) as resp:  # noqa: S310
            cert_pem = resp.read()
    except Exception as exc:
        logger.warning(f"Failed to fetch Alexa signing certificate from {cert_url}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not download certificate",
        ) from exc

    try:
        cert = x509.load_pem_x509_certificate(cert_pem)
    except Exception as exc:
        logger.warning(f"Invalid PEM certificate from {cert_url}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid certificate format",
        ) from exc

    _CERT_CACHE[cert_url] = (cert, now)
    return cert


def verify_alexa_signature(
    raw_body: bytes,
    signature_header: str,
    cert_url: str,
) -> None:
    """Verify the request signature using the certificate's public key."""
    cert = get_certificate(cert_url)

    now = datetime.now(UTC)
    if hasattr(cert, "not_valid_before_utc"):
        not_before = cert.not_valid_before_utc
    else:
        not_before = cert.not_valid_before.replace(tzinfo=UTC)

    if hasattr(cert, "not_valid_after_utc"):
        not_after = cert.not_valid_after_utc
    else:
        not_after = cert.not_valid_after.replace(tzinfo=UTC)

    if not (not_before <= now <= not_after):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signing certificate has expired or is not yet valid",
        )

    try:
        san_ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        dns_names = san_ext.value.get_values_for_type(x509.DNSName)
        if "echo-api.amazonaws.com" not in dns_names:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="echo-api.amazonaws.com not found in certificate Subject Alternative Names",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Failed to verify Subject Alternative Names in certificate",
        ) from exc

    try:
        sig_bytes = base64.b64decode(signature_header)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid base64 signature",
        ) from exc

    public_key = cert.public_key()
    try:
        public_key.verify(sig_bytes, raw_body, padding.PKCS1v15(), hashes.SHA1())  # noqa: S303
    except Exception as exc:
        logger.warning(f"Signature verification failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signature verification failed",
        ) from exc


def verify_request_timestamp(raw_body: bytes, max_age_seconds: int = 150) -> None:
    """Verify that the timestamp in the request body is within max_age_seconds of now."""
    try:
        data = json.loads(raw_body)
        timestamp_str = data.get("request", {}).get("timestamp")
        if not timestamp_str:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing timestamp in Alexa request",
            )
        req_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = datetime.now(UTC)
        if abs((now - req_time).total_seconds()) > max_age_seconds:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Request timestamp {timestamp_str} is older than {max_age_seconds}s",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Invalid timestamp in request: {exc}",
        ) from exc


async def verify_alexa_request(request: Request) -> bytes:
    """Verify ASK signature and timestamp if ALEXA_ENFORCE_SIGNATURE is enabled. Returns raw body."""
    raw_body = await request.body()
    settings = get_settings()

    if not settings.ALEXA_ENFORCE_SIGNATURE:
        return raw_body

    signature = request.headers.get("Signature")
    cert_url = request.headers.get("SignatureCertChainUrl")

    if not signature or not cert_url:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required Alexa signature headers (Signature or SignatureCertChainUrl)",
        )

    verify_request_timestamp(raw_body)
    verify_alexa_signature(raw_body, signature, cert_url)
    return raw_body
