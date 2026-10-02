"""SSRF Protection and URL Security Validator for RYVEN Health Engine (M13)."""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.parse
from typing import Optional, Tuple


class HealthSecurityValidator:
    """Validates URLs to ensure no internal network escape, SSRF, or malicious scheme."""

    ALLOWED_SCHEMES = {"http", "https"}

    # Disallowed hostnames
    BLOCKED_HOSTNAMES = {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata.google.internal",
        "metadata.internal",
        "instance-data",
    }

    # Blocked private and special CIDRs
    BLOCKED_NETWORKS = [
        ipaddress.ip_network("0.0.0.0/8"),          # Current network
        ipaddress.ip_network("10.0.0.0/8"),         # Private IPv4 class A
        ipaddress.ip_network("100.64.0.0/10"),      # Shared address / CGNAT
        ipaddress.ip_network("127.0.0.0/8"),        # Loopback IPv4
        ipaddress.ip_network("169.254.0.0/16"),     # Link-local / Cloud Metadata
        ipaddress.ip_network("172.16.0.0/12"),      # Private IPv4 class B
        ipaddress.ip_network("192.0.0.0/24"),       # IETF Protocol Assignments
        ipaddress.ip_network("192.0.2.0/24"),       # TEST-NET-1
        ipaddress.ip_network("192.168.0.0/16"),     # Private IPv4 class C
        ipaddress.ip_network("198.18.0.0/15"),      # Benchmarking
        ipaddress.ip_network("198.51.100.0/24"),    # TEST-NET-2
        ipaddress.ip_network("203.0.113.0/24"),     # TEST-NET-3
        ipaddress.ip_network("224.0.0.0/4"),        # Multicast
        ipaddress.ip_network("240.0.0.0/4"),        # Reserved
        ipaddress.ip_network("255.255.255.255/32"), # Broadcast
        ipaddress.ip_network("::1/128"),            # Loopback IPv6
        ipaddress.ip_network("::/128"),             # Unspecified IPv6
        ipaddress.ip_network("fc00::/7"),           # Unique local IPv6
        ipaddress.ip_network("fe80::/10"),          # Link-local IPv6
    ]

    # Malicious scheme and character patterns
    SUSPICIOUS_CHARS_PATTERN = re.compile(r"[<>\s\"'\{\}\\^`|]")
    UNC_PATTERN = re.compile(r"^[\\/]{2}")

    @classmethod
    def is_ip_blocked(cls, ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        """Check if an IP address belongs to any blocked/private network."""
        for network in cls.BLOCKED_NETWORKS:
            if ip_obj in network:
                return True
        return False

    @classmethod
    def sanitize_url(cls, url: str) -> str:
        """Remove any embedded credentials (user:password@) from a URL string."""
        if not url:
            return ""
        parsed = urllib.parse.urlsplit(url)
        if parsed.username or parsed.password:
            # Reconstruct netloc without credentials
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
        return url

    @classmethod
    def validate_url(cls, url: str, resolve_dns: bool = True) -> Tuple[bool, str, str]:
        """Validate URL for syntactic safety and SSRF protection.
        
        Returns:
            (is_safe: bool, sanitized_url: str, reason: str)
        """
        if not url or not isinstance(url, str):
            return False, "", "URL must be a non-empty string."

        cleaned = url.strip()

        # 1. Reject UNC and backslash paths
        if cls.UNC_PATTERN.search(cleaned) or "\\" in cleaned:
            return False, "", "Security violation: UNC and Windows backslash paths are prohibited."

        # 2. Reject suspicious control or shell characters
        if cls.SUSPICIOUS_CHARS_PATTERN.search(cleaned):
            return False, "", "Security violation: URL contains invalid or prohibited control characters."

        # 3. Parse URL
        try:
            parsed = urllib.parse.urlsplit(cleaned)
        except Exception as e:
            return False, "", f"Malformed URL syntax: {str(e)}"

        scheme = (parsed.scheme or "").lower()
        if scheme not in cls.ALLOWED_SCHEMES:
            return False, "", f"Disallowed URI scheme '{scheme}'. Only HTTP and HTTPS are permitted."

        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return False, "", "URL missing valid host name."

        # 4. Check explicit blocked hostnames
        if hostname in cls.BLOCKED_HOSTNAMES:
            return False, "", f"SSRF violation: Hostname '{hostname}' is blocked."

        # 5. Check if hostname is direct IP representation
        try:
            ip_obj = ipaddress.ip_address(hostname)
            if cls.is_ip_blocked(ip_obj):
                return False, "", f"SSRF violation: IP address '{hostname}' is within a private or reserved range."
        except ValueError:
            # Hostname is a domain name, not a plain IP
            pass

        # 6. Optional DNS resolution check to prevent DNS rebinding to private IPs
        if resolve_dns:
            try:
                addr_info = socket.getaddrinfo(hostname, None)
                for item in addr_info:
                    resolved_ip_str = item[4][0]
                    try:
                        resolved_ip = ipaddress.ip_address(resolved_ip_str)
                        if cls.is_ip_blocked(resolved_ip):
                            return False, "", f"SSRF violation: Host '{hostname}' resolved to blocked IP '{resolved_ip_str}'."
                    except ValueError:
                        continue
            except socket.gaierror:
                # DNS could not be resolved — let the probe handle unreachable host cleanly
                pass
            except Exception as e:
                # Any resolution failure is treated as untrusted
                return False, "", f"DNS validation error for '{hostname}': {str(e)}"

        sanitized = cls.sanitize_url(cleaned)
        return True, sanitized, ""
