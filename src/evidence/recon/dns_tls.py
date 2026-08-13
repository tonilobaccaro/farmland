"""DNS records and TLS certificate collection.

DNS resolution and the TLS handshake are both real network I/O, so the parsing
cores (`parse_cert`) are kept pure and unit-testable against a synthetic
`ssl`-shaped cert dict; only the outer `resolve_dns`/`fetch_tls_info`
functions need a live network to exercise end to end.
"""

from __future__ import annotations

import socket
import ssl
from dataclasses import asdict, dataclass, field


@dataclass
class DnsRecords:
    hostname: str
    a: list[str] = field(default_factory=list)
    aaaa: list[str] = field(default_factory=list)
    cname: list[str] = field(default_factory=list)
    mx: list[str] = field(default_factory=list)
    txt: list[str] = field(default_factory=list)
    resolved_ips: list[str] = field(default_factory=list)
    asn: list[dict] = field(default_factory=list)  # [{"ip": ..., "asn": None, "note": "..."}]
    errors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TlsInfo:
    subject: dict[str, str]
    issuer: dict[str, str]
    subject_alt_names: list[str]
    not_before: str | None
    not_after: str | None
    negotiated_protocol: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _dn_to_dict(dn: tuple) -> dict[str, str]:
    """Flatten the ((("commonName", "example.com"),), ...) shape ssl returns."""
    out: dict[str, str] = {}
    for rdn in dn:
        for key, value in rdn:
            out[key] = value
    return out


def parse_cert(cert: dict) -> TlsInfo:
    """Parse the dict shape returned by ssl.SSLSocket.getpeercert().

    Saving the SAN list explicitly matters: sibling API hostnames
    (api., idx., search.) frequently show up there.
    """
    sans = [value for kind, value in cert.get("subjectAltName", ()) if kind == "DNS"]
    return TlsInfo(
        subject=_dn_to_dict(cert.get("subject", ())),
        issuer=_dn_to_dict(cert.get("issuer", ())),
        subject_alt_names=sans,
        not_before=cert.get("notBefore"),
        not_after=cert.get("notAfter"),
    )


def lookup_asn_offline(ip: str) -> dict:
    """Best-effort offline ASN lookup. Requires a local MaxMind-style ASN
    database (not bundled with this project); if none is configured, records
    that plainly rather than silently returning nothing.
    """
    return {"ip": ip, "asn": None, "note": "no offline ASN database configured"}


async def resolve_dns(hostname: str) -> DnsRecords:
    """Resolve A/AAAA/CNAME/MX/TXT for hostname. Requires network + dnspython."""
    import dns.asyncresolver
    import dns.exception

    records = DnsRecords(hostname=hostname)
    resolver = dns.asyncresolver.Resolver()

    for rtype, attr in [("A", "a"), ("AAAA", "aaaa"), ("CNAME", "cname"), ("MX", "mx"), ("TXT", "txt")]:
        try:
            answer = await resolver.resolve(hostname, rtype)
            values = [str(r).strip('"') for r in answer]
            setattr(records, attr, values)
        except dns.exception.DNSException as exc:
            records.errors[rtype] = f"{type(exc).__name__}: {exc}"

    records.resolved_ips = list(dict.fromkeys(records.a + records.aaaa))
    records.asn = [lookup_asn_offline(ip) for ip in records.resolved_ips]
    return records


def fetch_tls_info(hostname: str, port: int = 443, timeout_s: float = 10.0) -> TlsInfo:
    """Open a TLS connection just far enough to read the peer certificate."""
    context = ssl.create_default_context()
    with (
        socket.create_connection((hostname, port), timeout=timeout_s) as sock,
        context.wrap_socket(sock, server_hostname=hostname) as tls_sock,
    ):
        cert = tls_sock.getpeercert()
        info = parse_cert(cert)
        info.negotiated_protocol = tls_sock.version()
        return info
