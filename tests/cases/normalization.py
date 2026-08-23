from __future__ import annotations

NORMALIZE_URL_CASES: tuple[tuple[str, str], ...] = (
    ("http://example.com:80", "http://example.com"),
    ("https://example.com:443", "https://example.com"),
    ("http://example.com:8080", "http://example.com:8080"),
    ("http://[::1]:8069", "http://[::1]:8069"),
    ("HTTP://EXAMPLE", "http://example"),
)

REJECT_URL_CASES: tuple[str, ...] = (
    "http://user:pass@example.com",
    "http://example.com?foo=bar",
    "http://example.com#frag",
    "http://example.com/path",
    "ftp://example.com",
    "not a url",
)

LOOPBACK_HOST_CASES: tuple[tuple[str, bool], ...] = (
    ("localhost", True),
    ("LOCALHOST", True),
    ("127.0.0.1", True),
    ("127.0.0.0", True),
    ("127.255.255.255", True),
    ("::1", True),
    ("10.0.0.1", False),
    ("192.168.0.1", False),
    ("172.16.0.1", False),
    ("example.com", False),
)

LOCAL_URL_CASES: tuple[str, ...] = (
    "http://localhost:8069",
    "http://LOCALHOST:8069",
    "http://127.0.0.1:8069",
    "http://127.1.2.3:8069",
    "http://127.255.255.255:8069",
    "http://[::1]:8069",
)

REMOTE_URL_CASES: tuple[str, ...] = (
    "http://example.com:8069",
    "http://192.168.0.1:8069",
    "http://10.0.0.1:8069",
)

BIND_HOST_CASES: tuple[tuple[str, str], ...] = (
    ("localhost", "127.0.0.1"),
    ("0.0.0.0", "0.0.0.0"),
    ("::", "::"),
)

VALID_DB_NAMES: tuple[str, ...] = (
    "comerta",
    "comerta_cmrt_123",
    "db_1",
    "a",
    "A.b-c_d",
    "_under",
    "1bad",
)

INVALID_DB_NAMES: tuple[str, ...] = (
    "../etc/passwd",
    "..",
    ".",
    "foo/bar",
    "foo\\bar",
    "foo\x00bar",
    "/abs",
    "-bad",
    "bad name",
    "",
    ".hidden",
)

REDACT_CASES: tuple[tuple[str, str], ...] = (
    ("master_pwd=secret", "master_pwd=***"),
    ('password="my pass"', 'password="***"'),
    ("admin_passwd='a b c'", "admin_passwd='***'"),
    ("name=mydb", "name=mydb"),
)

SANITIZE_SECRET_CASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        'password="my secret" token=abc123\n'
        'postgresql://alice:secret@example.test/db api_key="another key"',
        ("my secret", "abc123", "alice:secret", "another key"),
    ),
)
