import hashlib
import ipaddress
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import click
from peewee import (
    BooleanField,
    CharField,
    DatabaseProxy,
    DateTimeField,
    FloatField,
    ForeignKeyField,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
)

APP_NAME = "lcc"
DB_FILENAME = "lcc.db"
MEMORY = ":memory:"

PRAGMAS = {
    "journal_mode": "wal",  # Allow readers while writer active
    "cache_size": -6400,  # 64 MB page cache.
    "foreign_keys": 1,  # Enforce FK constraints.
    "busy_timeout": 5000,  # Wait 5s on a locked db instead of failing
}

TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "ref_src",
    }
)
BLOCKED_PORTS = frozenset({22, 23, 25, 445, 3306, 5432, 6379, 9200, 11211})
# is_private covers RFC1918, loopback, link-local, reserved, and unspecified.
# It does not cover these two.
EXTRA_BLOCKED_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64
)

db = DatabaseProxy()


class ValidationError(ValueError):
    """A model was saved with data that cannot be normalized into a valid row."""


def default_db_path() -> Path:
    """Absolute path to the database, independent of the working directory."""
    # roaming=False keeps the db machine-local
    return Path(click.get_app_dir(APP_NAME, roaming=False)) / DB_FILENAME


def init_db(
    path: str | Path | None = None, *, create_tables: bool = True
) -> SqliteDatabase:
    """Attach a real database to the proxy. Call once, before any query."""
    if path == MEMORY:
        database = SqliteDatabase(MEMORY, pragmas=PRAGMAS)
    else:
        resolved = Path(path).expanduser().resolve() if path else default_db_path()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        database = SqliteDatabase(resolved, pragmas=PRAGMAS)

    db.initialize(database)
    database.connect(reuse_if_open=True)

    if create_tables:
        database.create_tables(MODELS)

    return database


def utcnow() -> datetime:
    """Canonical stored form for every timestamp. timezone-aware UTC."""
    return datetime.now(timezone.utc)


def to_local(value: datetime | None) -> datetime | None:
    """Render a local timestamp in the timezone this program is running in."""
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone()


def is_blocked_address(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """True if this address is one we must never reach."""
    if mapped := getattr(ip, "ipv4_mapped", None):
        ip = mapped

    return (
        ip.is_private
        or ip.is_multicast
        or any(ip in net for net in EXTRA_BLOCKED_NETWORKS if net.version == ip.version)
    )


def normalize_url(raw: str) -> str:
    """
    Canonical form of a URL, for dedup and fetching. Lossy: drops the fragment.

    Enforces every guard that needs no network: scheme allowlist, port policy,
    and literal internal addresses. Hostnames need DNS, so fetch.guard handles
    those - keeping this pure and safe to call from clean() on every save.
    """
    try:
        parts = urlsplit((raw or "").strip())
        scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        raise ValidationError(f"Not a usable URL: {raw!r} ({exc})") from exc

    if not scheme or not host:
        raise ValidationError(f"Not a usable URL: {raw!r}")

    if scheme not in ("http", "https"):
        raise ValidationError(f"Unsupported scheme {scheme!r} in {raw!r}")

    if port in BLOCKED_PORTS:
        raise ValidationError(f"Refusing non-web port {port} in {raw!r}")

    # Drop redundant default ports so they dedup against the bare host.
    if (scheme, port) in (("http", 80), ("https", 443)):
        port = None

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if is_blocked_address(literal):
            raise ValidationError(f"Refusing to fetch internal address: {host!r}")

    if ":" in host:
        host = f"[{host}]"

    netloc = f"{host}:{port}" if port else host
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS
        ]
    )

    return urlunsplit((scheme, netloc, parts.path or "/", query, ""))


class Verdict(StrEnum):
    """How a claim fared against one source."""

    SUPPORTED_RELIABLE = "Trusted"
    SUPPORTED_UNRELIABLE = "Questionable"
    UNSUPPORTED_RELIABLE = "Unrelated"
    UNSUPPORTED_UNRELIABLE = "Untrusted"
    UNVERIFIABLE = "Unverifiable"  # Bot-blocked or transient. Not a judgment.


class FetchOutcome(StrEnum):
    """Why a fetch ended the way it did. Distinct from a citation verdict."""

    PENDING = "pending"  # Not attempted yet.
    OK = "ok"  # 2xx with real content.
    DNS_NXDOMAIN = "dns_nxdomain"  # Domain does not exist. Fabrication signal.
    DNS_ERROR = "dns_error"  # SERVFAIL etc. Says nothing about the URL.
    CONNECT_ERROR = "connect_error"
    TIMEOUT = "timeout"
    HTTP_NOT_FOUND = "http_not_found"  # 404 / 410. Check the archive before judging.
    HTTP_BLOCKED = "http_blocked"  # 403/405/429/503. Bot-blocked, NOT a verdict.
    HTTP_ERROR = "http_error"  # Other non-2xx.
    SOFT_NOT_FOUND = "soft_404"  # 200 but the body is an error page.


class BaseModel(Model):
    """All models inherit this to share the database connection."""

    class Meta:
        database = db

    def clean(self) -> None:
        """Normalize and validate this row in place. Runs before every save."""

    def save(self, *args, **kwargs):
        self.clean()

        return super().save(*args, **kwargs)


class TimeStampedModel(BaseModel):
    created_time = DateTimeField(verbose_name="Created at", default=utcnow)
    updated_time = DateTimeField(verbose_name="Updated at", null=True)

    def save(self, *args, **kwargs):
        self.updated_time = utcnow()

        return super().save(*args, **kwargs)

    @property
    def created_local(self) -> datetime | None:
        return to_local(self.created_time)

    @property
    def updated_local(self) -> datetime | None:
        return to_local(self.updated_time)


class Source(TimeStampedModel):
    """A URL as a fetchable resource. One row per URL, reused across claims."""

    url = TextField(verbose_name="URL", unique=True)
    domain = CharField(verbose_name="Domain", index=True, null=True)
    resolved_url = TextField(verbose_name="Resolved URL", null=True)
    fetch_outcome = CharField(
        verbose_name="Fetch Outcome", default=FetchOutcome.PENDING, index=True
    )
    http_status = IntegerField(verbose_name="HTTP Status", null=True)
    extracted_text = TextField(verbose_name="Extracted Text", null=True)
    content_hash = CharField(verbose_name="Content Hash", max_length=64, null=True)
    is_reliable = BooleanField(verbose_name="Is Reliable", null=True)

    def clean(self) -> None:
        self.url = normalize_url(self.url)
        self.domain = urlsplit(self.url).hostname

        if self.fetch_outcome not in FetchOutcome:
            raise ValidationError(f"Unknown fetch outcome: {self.fetch_outcome!r}")

        self.content_hash = (
            hashlib.sha256(self.extracted_text.encode("utf-8")).hexdigest()
            if self.extracted_text
            else None
        )

    @classmethod
    def for_url(cls, url: str) -> "Source":
        """Get the row for this URL, creating a PENDING one if it is new."""
        canonical = normalize_url(url)
        source, _ = cls.get_or_create(url=canonical)

        return source

    def record_fetch(
        self,
        outcome: FetchOutcome,
        *,
        http_status: int | None = None,
        resolved_url: str | None = None,
        text: str | None = None,
    ) -> None:
        """Store the result of one fetch attempt. content_hash recomputes itself."""
        self.fetch_outcome = outcome
        self.http_status = http_status
        self.resolved_url = resolved_url
        self.extracted_text = text
        self.save()


class Claim(TimeStampedModel):
    """A statement extracted from the checked document that cites something."""

    claim_text = TextField(verbose_name="Claim")
    document_path = CharField(null=True)  # Becomes FK to Document later
    char_offset = IntegerField(null=True)  # Offset into the original document

    def clean(self) -> None:
        self.claim_text = " ".join((self.claim_text or "").split())

        if not self.claim_text:
            raise ValidationError("A claim must have text.")


class Citation(BaseModel):
    """One claim leaning on one source. The verdict lives here, not on either side."""

    class Meta:
        indexes = ((("claim", "source"), True),)

    claim = ForeignKeyField(Claim, backref="citations", on_delete="CASCADE")
    source = ForeignKeyField(Source, backref="citations", on_delete="CASCADE")
    cited_url = TextField(null=True)
    verdict = CharField(null=True, index=True)
    confidence = FloatField(null=True)

    def clean(self) -> None:
        if self.verdict is not None and self.verdict not in Verdict:
            raise ValidationError(f"Unknown verdict: {self.verdict!r}")

    @classmethod
    def record(
        cls,
        claim: Claim,
        source: Source,
        *,
        cited_url: str | None = None,
        verdict: Verdict | None = None,
        confidence: float | None = None,
    ) -> "Citation":
        """Link a claim to a source, updating the verdict if the link exists."""
        citation, created = cls.get_or_create(
            claim=claim,
            source=source,
            defaults={
                "cited_url": cited_url,
                "verdict": verdict,
                "confidence": confidence,
            },
        )

        if not created and verdict is not None:
            citation.verdict = verdict
            citation.confidence = confidence
            citation.save()

        return citation


MODELS = (Source, Claim, Citation)
