import socket
import time
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.providers import remote_cover
from mechanical_librarian.adapters.providers.remote_cover import (
    CoverDeadline,
    RemoteCover,
    RemoteCoverError,
    RemoteCoverTimeout,
    _pinned_address,
    fetch_remote_cover,
)
from mechanical_librarian.application.metadata_report import CoverRedirects


class _FakeSocket:
    def __init__(self, headers: bytes, body: bytes = b"") -> None:
        self.headers = bytearray(headers)
        self.body = bytearray(body)
        self.body_bytes_read = 0
        self.maximum_recv = 0
        self.sent = bytearray()

    def __enter__(self) -> "_FakeSocket":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def close(self) -> None:
        return None

    def getpeername(self) -> tuple[str, int]:
        return ("8.8.8.8", 443)

    def recv(self, maximum: int) -> bytes:
        self.maximum_recv = max(self.maximum_recv, maximum)
        source = self.headers if self.headers else self.body
        if not source:
            return b""
        size = min(maximum, len(source))
        value = bytes(source[:size])
        del source[:size]
        if source is self.body:
            self.body_bytes_read += size
        return value

    def sendall(self, value: bytes) -> None:
        self.sent.extend(value)

    def settimeout(self, _value: float) -> None:
        return None


class _PinnedPeerSocket:
    """Expose one connected local test peer as the pinned TLS transport."""

    def __init__(self, value: socket.socket) -> None:
        self._value = value

    def __enter__(self) -> "_PinnedPeerSocket":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def close(self) -> None:
        self._value.close()

    def getpeername(self) -> tuple[str, int]:
        return ("8.8.8.8", 443)

    def recv(self, maximum: int) -> bytes:
        return self._value.recv(maximum)

    def sendall(self, value: bytes) -> None:
        self._value.sendall(value)

    def settimeout(self, value: float) -> None:
        self._value.settimeout(value)


class _FakeContext:
    """Record each TLS host name; assert it when one host is expected."""

    def __init__(self, expected: str | None = "example.com") -> None:
        self.expected = expected
        self.hostnames: list[str] = []

    def wrap_socket(self, raw: _FakeSocket, *, server_hostname: str) -> _FakeSocket:
        if self.expected is not None and server_hostname != self.expected:
            raise AssertionError(server_hostname)
        self.hostnames.append(server_hostname)
        return raw


def test_rejects_non_https_and_private_address() -> None:
    with pytest.raises(RemoteCoverError):
        _pinned_address("http://example.com/a.jpg")
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
        with pytest.raises(RemoteCoverError):
            _pinned_address("https://example.com/a.jpg")


def test_accepts_public_https_443_only() -> None:
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
        assert ("example.com", "8.8.8.8", "/a.jpg") == _pinned_address(
            "https://example.com/a.jpg"
        )
    with pytest.raises(RemoteCoverError):
        _pinned_address("https://example.com:444/a.jpg")


def test_rejects_mixed_and_reserved_dns_answers_and_url_smuggling() -> None:
    with patch(
        "socket.getaddrinfo",
        return_value=[
            (2, 1, 6, "", ("8.8.8.8", 443)),
            (2, 1, 6, "", ("192.168.1.1", 443)),
        ],
    ):
        with pytest.raises(RemoteCoverError):
            _pinned_address("https://example.com/a.jpg")
    for value in (
        "https://user@example.com/a.jpg",
        "https://example.com/a.jpg#fragment",
    ):
        with pytest.raises(RemoteCoverError):
            _pinned_address(value)


@pytest.mark.parametrize(
    "unresolvable",
    ("https://example.com:99999/cover.png", "https://[::1/cover.png"),
    ids=("port_out_of_range", "malformed_ipv6"),
)
def test_invalid_port_and_malformed_ipv6_fail_before_dns(unresolvable: str) -> None:
    with patch("socket.getaddrinfo") as lookup:
        with pytest.raises(RemoteCoverError, match="unsafe remote cover"):
            _pinned_address(unresolvable)
        lookup.assert_not_called()
    for value in (
        "https://example.com/a b.jpg",
        "https://example.com/a\x00b.jpg",
        "https://example.com/café.jpg",
        "https://example.com/a\\b.jpg",
    ):
        with pytest.raises(RemoteCoverError):
            _pinned_address(value)


def test_fetch_uses_only_explicit_host_and_identity_headers() -> None:
    secured = _FakeSocket(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: 5\r\n\r\n",
        b"cover",
    )
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("socket.create_connection", return_value=secured),
        patch("ssl.create_default_context", return_value=_FakeContext()),
    ):
        value = fetch_remote_cover("https://example.com/cover.png")
    assert b"cover" == value.content
    request = bytes(secured.sent).decode("ascii")
    assert "Host: example.com\r\n" in request
    assert "Accept-Encoding: identity\r\n" in request
    assert "Authorization:" not in request
    assert "Cookie:" not in request
    assert "Referer:" not in request
    assert "User-Agent:" not in request


@pytest.mark.parametrize(
    "rejected_agent",
    ("", "unsafe\r\nAuthorization: value", "é"),
    ids=("empty", "header_injection", "non_ascii"),
)
def test_fetch_can_use_one_validated_identified_user_agent(
    rejected_agent: str,
) -> None:
    secured = _FakeSocket(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: 5\r\n\r\n",
        b"cover",
    )
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("socket.create_connection", return_value=secured),
        patch("ssl.create_default_context", return_value=_FakeContext()),
    ):
        fetch_remote_cover(
            "https://example.com/cover.png",
            user_agent="Mechanical-Librarian (operator@example.invalid)",
        )
    assert "User-Agent: Mechanical-Librarian (operator@example.invalid)\r\n" in bytes(
        secured.sent
    ).decode("ascii")
    with pytest.raises(RemoteCoverError):
        fetch_remote_cover(
            "https://example.com/cover.png",
            user_agent=rejected_agent,
        )


def test_rejects_content_encoding_and_any_redirect_by_default() -> None:
    encoded = _FakeSocket(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Encoding: gzip\r\nContent-Length: 1\r\n\r\n",
        b"x",
    )
    redirect = _FakeSocket(
        b"HTTP/1.1 302 Found\r\nLocation: /again\r\nContent-Length: 0\r\n\r\n"
    )
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("ssl.create_default_context", return_value=_FakeContext()),
    ):
        with (
            patch("socket.create_connection", return_value=encoded),
            pytest.raises(RemoteCoverError),
        ):
            fetch_remote_cover("https://example.com/cover.png")
        with (
            patch("socket.create_connection", side_effect=[redirect]) as connect,
            pytest.raises(RemoteCoverError, match="^remote cover unavailable$"),
        ):
            fetch_remote_cover("https://example.com/cover.png")
        connect.assert_called_once()


def test_redirects_can_be_rejected_after_one_pinned_request() -> None:
    redirect = _FakeSocket(
        b"HTTP/1.1 302 Found\r\nLocation: /again\r\nContent-Length: 0\r\n\r\n"
    )
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("socket.create_connection", return_value=redirect) as connect,
        patch("ssl.create_default_context", return_value=_FakeContext()),
        pytest.raises(RemoteCoverError),
    ):
        fetch_remote_cover(
            "https://example.com/cover.png",
            redirects=CoverRedirects.NONE,
        )
    connect.assert_called_once()


def test_elapsed_aggregate_deadline_permits_no_dns_or_request() -> None:
    with patch("socket.getaddrinfo") as lookup:
        with pytest.raises(RemoteCoverTimeout):
            fetch_remote_cover(
                "https://example.com/cover.png", deadline=time.monotonic() - 1
            )
    lookup.assert_not_called()


def test_production_path_connects_to_one_validated_address_without_reresolving() -> (
    None
):
    with (
        patch(
            "socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]
        ) as lookup,
        patch("socket.create_connection", side_effect=OSError),
    ):
        with pytest.raises(RemoteCoverError):
            fetch_remote_cover("https://example.com/cover.png")
    lookup.assert_called_once_with("example.com", 443, type=socket.SOCK_STREAM)


@pytest.mark.parametrize(
    ("headers", "body"),
    (
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 4\r\n\r\n",
            b"html",
        ),
        (b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\n\r\n", b"missing"),
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: image/png; charset=binary\r\nContent-Length: 6\r\n\r\n",
            b"params",
        ),
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Encoding: gzip\r\nContent-Length: 4\r\n\r\n",
            b"gzip",
        ),
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Encoding:\r\nContent-Length: 5\r\n\r\n",
            b"empty",
        ),
        (
            b"HTTP/1.1 404 Not Found\r\nContent-Type: image/png\r\nContent-Length: 5\r\n\r\n",
            b"error",
        ),
    ),
    ids=(
        "html_content_type",
        "absent_content_type",
        "parameterized_content_type",
        "gzip_content_encoding",
        "empty_content_encoding",
        "not_found_status",
    ),
)
def test_production_path_gates_headers_before_reading_rejected_bodies(
    headers: bytes, body: bytes
) -> None:
    secured = _FakeSocket(headers, body)
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("socket.create_connection", return_value=secured),
        patch("ssl.create_default_context", return_value=_FakeContext()),
        pytest.raises(RemoteCoverError),
    ):
        fetch_remote_cover("https://example.com/cover.png")
    assert 0 == secured.body_bytes_read


def test_production_path_redirect_does_not_read_body_and_valid_response_does() -> None:
    redirect = _FakeSocket(
        b"HTTP/1.1 302 Found\r\nLocation: /final.png\r\nContent-Length: 8\r\n\r\n",
        b"redirect",
    )
    valid = _FakeSocket(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: 5\r\n\r\n",
        b"cover",
    )
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("socket.create_connection", side_effect=[redirect, valid]),
        patch("ssl.create_default_context", return_value=_FakeContext()),
    ):
        with pytest.raises(RemoteCoverError):
            fetch_remote_cover("https://example.com/cover.png")
        result = fetch_remote_cover("https://example.com/final.png")
    assert b"cover" == result.content
    assert 0 == redirect.body_bytes_read
    assert 5 == valid.body_bytes_read


def test_production_path_reads_close_delimited_image_body() -> None:
    secured = _FakeSocket(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n\r\n",
        b"close-delimited-cover",
    )
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("socket.create_connection", return_value=secured),
        patch("ssl.create_default_context", return_value=_FakeContext()),
    ):
        result = fetch_remote_cover("https://example.com/cover.png")
    assert b"close-delimited-cover" == result.content
    assert len(result.content) == secured.body_bytes_read


def test_pinned_fetch_times_out_when_a_peer_stalls_mid_body() -> None:
    client, peer = socket.socketpair()
    secured = _PinnedPeerSocket(client)
    try:
        peer.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: 9\r\n\r\ncover"
        )
        with (
            patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
            patch("socket.create_connection", return_value=secured),
            patch("ssl.create_default_context", return_value=_FakeContext()),
            pytest.raises(RemoteCoverTimeout),
        ):
            fetch_remote_cover(
                "https://example.com/cover.png", deadline=time.monotonic() + 0.1
            )
    finally:
        peer.close()


def test_close_delimited_body_obeys_cap_and_deadline() -> None:
    oversized = _FakeSocket(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n\r\n",
        b"x" * (remote_cover.MAX_BYTES + 1),
    )
    with pytest.raises(RemoteCoverError):
        remote_cover._response_body(oversized, {}, time.monotonic() + 1)
    with pytest.raises(RemoteCoverTimeout):
        remote_cover._response_body(
            _FakeSocket(b"", b"cover"), {}, time.monotonic() - 1
        )


def test_native_socket_timeout_is_timeout_disposition() -> None:
    with patch.object(remote_cover, "_pinned_fetch", side_effect=socket.timeout()):
        with pytest.raises(RemoteCoverTimeout):
            fetch_remote_cover("https://example.com/cover.png")


def test_production_path_bounds_unterminated_chunk_framing() -> None:
    secured = _FakeSocket(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nTransfer-Encoding: chunked\r\n\r\n",
        b"A" * (remote_cover._MAX_CHUNK_LINE_BYTES + 64),
    )
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("socket.create_connection", return_value=secured),
        patch("ssl.create_default_context", return_value=_FakeContext()),
        pytest.raises(RemoteCoverError),
    ):
        fetch_remote_cover("https://example.com/cover.png")
    assert remote_cover._MAX_CHUNK_LINE_BYTES == secured.body_bytes_read
    assert secured.maximum_recv <= remote_cover._MAX_CHUNK_LINE_BYTES


# Open Library's closed archive route (#1752). Every id, item, zip, datanode and
# Location below is synthetic.
_COVER_URL = "https://covers.openlibrary.org/b/id/7-L.jpg?default=false"
_HOP1 = "https://archive.org/download/olcovers0/olcovers0-L.zip/7-L.jpg"
_HOP2 = (
    "https://ia800100.us.archive.org/view_archive.php"
    "?archive=/12/items/olcovers0/olcovers0-L.zip&file=7-L.jpg"
)
_PADDED_HOP1 = (
    "https://archive.org/download/l_covers_0000/l_covers_0000_00.zip/0000000007-L.jpg"
)
_PADDED_HOP2 = (
    "https://ia600100.us.archive.org/view_archive.php"
    "?archive=/34/items/l_covers_0000/l_covers_0000_00.zip&file=0000000007-L.jpg"
)
_AGENT = "Mechanical-Librarian (operator@example.invalid)"
_COVERS_HOST = "covers.openlibrary.org"
_ARCHIVE_HOST = "archive.org"


def _redirect(location: str) -> _FakeSocket:
    return _FakeSocket(
        b"HTTP/1.1 302 Found\r\nLocation: "
        + location.encode("ascii")
        + b"\r\nContent-Length: 8\r\n\r\n",
        b"redirect",
    )


def _image(body: bytes = b"cover") -> _FakeSocket:
    return _FakeSocket(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: "
        + str(len(body)).encode("ascii")
        + b"\r\n\r\n",
        body,
    )


class _Route:
    """Fake DNS, pinned sockets and TLS for each hop of one closed chain."""

    def __init__(
        self, *sockets: object, answers: dict[str, tuple[str, ...]] | None = None
    ) -> None:
        self.sockets = sockets
        self.answers = answers or {}
        self.lookups: list[str] = []
        self.context = _FakeContext(None)
        self.connects = 0

    def _lookup(
        self, host: str, port: int, type: int
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        assert (port, type) == (443, socket.SOCK_STREAM)
        self.lookups.append(host)
        return [
            (2, 1, 6, "", (address, 443))
            for address in self.answers.get(host, ("8.8.8.8",))
        ]

    def _connect(self, address: tuple[str, int], timeout: float) -> object:
        assert address == ("8.8.8.8", 443)
        assert 0 < timeout <= 5.0
        value = self.sockets[self.connects]
        self.connects += 1
        return value

    def fetch(
        self,
        url: str = _COVER_URL,
        *,
        deadline: CoverDeadline | float | None = None,
        user_agent: str | None = _AGENT,
    ) -> RemoteCover:
        with (
            patch("socket.getaddrinfo", side_effect=self._lookup),
            patch("socket.create_connection", side_effect=self._connect),
            patch("ssl.create_default_context", return_value=self.context),
        ):
            return fetch_remote_cover(
                url,
                deadline=CoverDeadline.start() if deadline is None else deadline,
                redirects=CoverRedirects.OPEN_LIBRARY_ARCHIVE,
                user_agent=user_agent,
            )


def _request(host: str, path: str) -> bytes:
    return (
        f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
        "Accept-Encoding: identity\r\nConnection: close\r\n\r\n"
    ).encode("ascii")


@pytest.mark.parametrize(
    ("hop1", "hop2", "datanode"),
    (
        (_HOP1, _HOP2, "ia800100.us.archive.org"),
        (_PADDED_HOP1, _PADDED_HOP2, "ia600100.us.archive.org"),
    ),
    ids=("olcovers_layout", "zero_padded_l_covers_layout"),
)
def test_archive_route_follows_two_bound_redirects_to_a_datanode(
    hop1: str, hop2: str, datanode: str
) -> None:
    first, second, final = _redirect(hop1), _redirect(hop2), _image()
    route = _Route(first, second, final)
    result = route.fetch()
    assert (result.url, result.media_type, result.content) == (
        hop2,
        "image/jpeg",
        b"cover",
    )
    hosts = [_COVERS_HOST, _ARCHIVE_HOST, datanode]
    assert route.connects == 3
    assert route.lookups == hosts
    assert route.context.hostnames == hosts
    # Only position 0 carries the User-Agent with the contact email; positions
    # 1 and 2 send exactly Host, Accept-Encoding and Connection.
    assert bytes(first.sent) == (
        "GET /b/id/7-L.jpg?default=false HTTP/1.1\r\n"
        f"Host: {_COVERS_HOST}\r\nUser-Agent: {_AGENT}\r\n"
        "Accept-Encoding: identity\r\nConnection: close\r\n\r\n"
    ).encode("ascii")
    assert bytes(second.sent) == _request(
        _ARCHIVE_HOST, hop1.removeprefix("https://archive.org")
    )
    assert bytes(final.sent) == _request(
        datanode, hop2.removeprefix(f"https://{datanode}")
    )
    assert (first.body_bytes_read, second.body_bytes_read) == (0, 0)
    assert final.body_bytes_read == 5


@pytest.mark.parametrize(
    ("hop1", "hop2"),
    (
        (
            "https://archive.org:443/download/olcovers0/olcovers0-L.zip/7-L.jpg",
            "https://ia1.us.archive.org:443/view_archive.php"
            "?file=7-L.jpg&archive=/1/items/olcovers0/olcovers0-L.zip",
        ),
        (
            _HOP1,
            "https://ia123456.us.archive.org/view_archive.php"
            "?archive=/1234/items/olcovers0/olcovers0-L.zip&file=7-L.jpg",
        ),
    ),
    ids=("explicit_443_and_reverse_key_order", "widest_datanode_and_prefix"),
)
def test_archive_route_accepts_the_grammar_edges(hop1: str, hop2: str) -> None:
    route = _Route(_redirect(hop1), _redirect(hop2), _image())
    assert route.fetch().content == b"cover"
    assert route.connects == 3


def test_archive_route_ends_at_a_direct_answer_from_either_allowed_step() -> None:
    direct = _Route(_image())
    assert direct.fetch().url == _COVER_URL
    assert direct.connects == 1
    archive = _Route(_redirect(_HOP1), _image())
    assert archive.fetch().url == _HOP1
    assert (archive.connects, archive.lookups) == (2, [_COVERS_HOST, _ARCHIVE_HOST])


_HOP1_REFUSALS = {
    "off_list_host": "https://evil.example/download/a/b.zip/7-L.jpg",
    "suffix_host": "https://archive.org.evil.example/download/a/b.zip/7-L.jpg",
    "prefixed_host": "https://xarchive.org/download/a/b.zip/7-L.jpg",
    "datanode_at_hop_1": "https://ia1.us.archive.org/download/a/b.zip/7-L.jpg",
    "punycode_host": "https://xn--archive-9za.org/download/a/b.zip/7-L.jpg",
    "trailing_dot_host": "https://archive.org./download/a/b.zip/7-L.jpg",
    "uppercase_host": "https://ARCHIVE.ORG/download/a/b.zip/7-L.jpg",
    "uppercase_scheme": "HTTPS://archive.org/download/a/b.zip/7-L.jpg",
    "http_downgrade": "http://archive.org/download/a/b.zip/7-L.jpg",
    "ipv4_literal": "https://8.8.8.8/download/a/b.zip/7-L.jpg",
    "ipv6_literal": "https://[2001:4860::8888]/download/a/b.zip/7-L.jpg",
    "port_8443": "https://archive.org:8443/download/a/b.zip/7-L.jpg",
    "userinfo": "https://user@archive.org/download/a/b.zip/7-L.jpg",
    "at_in_path": "https://archive.org/download/a@b/b.zip/7-L.jpg",
    "fragment": "https://archive.org/download/a/b.zip/7-L.jpg#x",
    "empty_fragment": "https://archive.org/download/a/b.zip/7-L.jpg#",
    "query": "https://archive.org/download/a/b.zip/7-L.jpg?x=1",
    "empty_query": "https://archive.org/download/a/b.zip/7-L.jpg?",
    "percent_dot_segment": "https://archive.org/download/%2e%2e/b.zip/7-L.jpg",
    "dot_dot_segment": "https://archive.org/download/../b.zip/7-L.jpg",
    "dot_segment": "https://archive.org/download/./b.zip/7-L.jpg",
    "backslash": "https://archive.org/download/a\\b/b.zip/7-L.jpg",
    "tab_in_scheme": "htt\tps://archive.org/download/a/b.zip/7-L.jpg",
    "tab_in_host": "https://arch\tive.org/download/a/b.zip/7-L.jpg",
    "line_feed_in_host": "https://ev\nil.example/download/a/b.zip/7-L.jpg",
    "trailing_line_feed": "https://archive.org/download/a/b.zip/7-L.jpg\n",
    "space_in_path": "https://archive.org/download/a b/b.zip/7-L.jpg",
    "segment_too_long": "https://archive.org/download/" + "a" * 65 + "/b.zip/7-L.jpg",
    "extra_segment": "https://archive.org/download/a/b.zip/c/7-L.jpg",
    "missing_segment": "https://archive.org/download/a/7-L.jpg",
    "other_path": "https://archive.org/details/a/b.zip/7-L.jpg",
    "cover_id_mismatch": "https://archive.org/download/a/b.zip/2-L.jpg",
    "medium_size": "https://archive.org/download/a/b.zip/7-M.jpg",
    "partial_padding": "https://archive.org/download/a/b.zip/07-L.jpg",
    "over_padding": "https://archive.org/download/a/b.zip/00000000007-L.jpg",
    "loop_to_open_library": _COVER_URL,
    "relative": "/download/a/b.zip/7-L.jpg",
    "scheme_relative": "//archive.org/download/a/b.zip/7-L.jpg",
}


@pytest.mark.parametrize(
    "location", tuple(_HOP1_REFUSALS.values()), ids=tuple(_HOP1_REFUSALS)
)
def test_archive_route_refuses_an_off_route_first_redirect_before_any_lookup(
    location: str,
) -> None:
    route = _Route(_redirect(location), _image(), _image())
    with pytest.raises(RemoteCoverError, match="^unsafe remote redirect$"):
        route.fetch()
    assert (route.connects, route.lookups) == (1, [_COVERS_HOST])


_DATANODE = "https://ia1.us.archive.org/view_archive.php"
_QUERY = "archive=/12/items/olcovers0/olcovers0-L.zip&file=7-L.jpg"
_HOP2_REFUSALS = {
    "loop_to_archive_org": _HOP1,
    "loop_to_open_library": _COVER_URL,
    "eu_datanode": "https://ia1.eu.archive.org/view_archive.php?" + _QUERY,
    "non_numeric_datanode": "https://iaX.us.archive.org/view_archive.php?" + _QUERY,
    "seven_digit_datanode": "https://ia1234567.us.archive.org/view_archive.php?"
    + _QUERY,
    "uppercase_datanode": "https://IA1.US.ARCHIVE.ORG/view_archive.php?" + _QUERY,
    "trailing_dot_datanode": "https://ia1.us.archive.org./view_archive.php?" + _QUERY,
    "line_feed_after_host": "https://ia1.us.archive.org\n/view_archive.php?" + _QUERY,
    "trailing_line_feed": _DATANODE + "?" + _QUERY + "\n",
    "tab_in_query": _DATANODE
    + "?archive=/12/items/olcovers0/olcovers0-L.zip&\tfile=7-L.jpg",
    "http_downgrade": "http://ia1.us.archive.org/view_archive.php?" + _QUERY,
    "port_8443": "https://ia1.us.archive.org:8443/view_archive.php?" + _QUERY,
    "userinfo": "https://user@ia1.us.archive.org/view_archive.php?" + _QUERY,
    "fragment": _DATANODE + "?" + _QUERY + "#x",
    "empty_fragment": _DATANODE + "?" + _QUERY + "#",
    "other_path": "https://ia1.us.archive.org/view_archive.php/?" + _QUERY,
    "no_query": _DATANODE,
    "empty_query": _DATANODE + "?",
    "file_not_bound": _DATANODE
    + "?archive=/12/items/olcovers0/olcovers0-L.zip&file=2-L.jpg",
    "item_not_bound": _DATANODE
    + "?archive=/12/items/other/olcovers0-L.zip&file=7-L.jpg",
    "zip_not_bound": _DATANODE + "?archive=/12/items/olcovers0/other.zip&file=7-L.jpg",
    "five_digit_prefix": _DATANODE
    + "?archive=/12345/items/olcovers0/olcovers0-L.zip&file=7-L.jpg",
    "missing_prefix": _DATANODE
    + "?archive=/items/olcovers0/olcovers0-L.zip&file=7-L.jpg",
    "duplicate_file": _DATANODE + "?" + _QUERY + "&file=7-L.jpg",
    "extra_key": _DATANODE + "?" + _QUERY + "&x=1",
    "missing_file": _DATANODE + "?archive=/12/items/olcovers0/olcovers0-L.zip",
    "semicolon_separator": _DATANODE
    + "?archive=/12/items/olcovers0/olcovers0-L.zip;file=7-L.jpg",
    "plus_in_value": _DATANODE + "?" + _QUERY + "+",
    "percent_in_value": _DATANODE
    + "?archive=/12/items/olcovers0/olcovers0-L.zip&file=7%2DL.jpg",
    "percent_encoded_slash": _DATANODE
    + "?archive=%2F12%2Fitems%2Folcovers0%2Folcovers0-L.zip&file=7-L.jpg",
    "two_equals": _DATANODE
    + "?archive=/12/items/olcovers0/olcovers0-L.zip&file==7-L.jpg",
    "empty_pair": _DATANODE
    + "?archive=/12/items/olcovers0/olcovers0-L.zip&&file=7-L.jpg",
    "relative": "/view_archive.php?" + _QUERY,
}


@pytest.mark.parametrize(
    "location", tuple(_HOP2_REFUSALS.values()), ids=tuple(_HOP2_REFUSALS)
)
def test_archive_route_refuses_an_off_route_second_redirect_before_any_lookup(
    location: str,
) -> None:
    route = _Route(_redirect(_HOP1), _redirect(location), _image())
    with pytest.raises(RemoteCoverError, match="^unsafe remote redirect$"):
        route.fetch()
    assert (route.connects, route.lookups) == (2, [_COVERS_HOST, _ARCHIVE_HOST])


def test_archive_route_refuses_a_third_redirect() -> None:
    route = _Route(_redirect(_HOP1), _redirect(_HOP2), _redirect(_HOP2), _image())
    with pytest.raises(RemoteCoverError, match="^unsafe remote redirect$"):
        route.fetch()
    assert route.connects == 3
    assert route.lookups == [_COVERS_HOST, _ARCHIVE_HOST, "ia800100.us.archive.org"]


@pytest.mark.parametrize(
    "sockets",
    (
        (_FakeSocket(b"HTTP/1.1 302 Found\r\nContent-Length: 0\r\n\r\n"),),
        (
            _redirect(_HOP1),
            _FakeSocket(
                b"HTTP/1.1 302 Found\r\nLocation:\r\nContent-Length: 0\r\n\r\n"
            ),
        ),
    ),
    ids=("hop_1", "hop_2"),
)
def test_archive_route_missing_location_stays_unavailable(
    sockets: tuple[_FakeSocket, ...],
) -> None:
    route = _Route(*sockets)
    with pytest.raises(RemoteCoverError, match="^remote cover unavailable$"):
        route.fetch()
    assert route.connects == len(sockets)


@pytest.mark.parametrize(
    "addresses",
    (
        ("10.0.0.1",),
        ("127.0.0.1",),
        ("169.254.169.254",),
        ("100.64.0.1",),
        ("8.8.8.8", "192.168.1.1"),
        ("::1",),
    ),
    ids=("private", "loopback", "link_local", "shared_space", "mixed", "ipv6_loopback"),
)
@pytest.mark.parametrize("position", (1, 2), ids=("archive_org", "datanode"))
def test_archive_route_refuses_non_global_answers_at_each_hop(
    addresses: tuple[str, ...], position: int
) -> None:
    host = _ARCHIVE_HOST if position == 1 else "ia800100.us.archive.org"
    route = _Route(
        _redirect(_HOP1), _redirect(_HOP2), _image(), answers={host: addresses}
    )
    with pytest.raises(RemoteCoverError, match="^unsafe remote cover$"):
        route.fetch()
    assert route.connects == position
    assert route.lookups[-1] == host


@pytest.mark.parametrize(
    ("final", "most_read"),
    (
        (
            _FakeSocket(
                b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(remote_cover.MAX_BYTES + 1).encode("ascii")
                + b"\r\n\r\n",
                b"x" * 16,
            ),
            0,
        ),
        (
            _FakeSocket(
                b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n",
                b"800000\r\n"
                + b"x" * remote_cover.MAX_BYTES
                + b"\r\n1\r\nx\r\n0\r\n\r\n",
            ),
            # The cap is checked on the second chunk's size line, before any
            # of its data; the framing reader takes at most one 4 KiB read.
            remote_cover.MAX_BYTES + 8 + 4_096,
        ),
        (
            _FakeSocket(
                b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n\r\n",
                b"x" * (remote_cover.MAX_BYTES + 65_536),
            ),
            remote_cover.MAX_BYTES + 1,
        ),
    ),
    ids=("content_length", "chunked", "close_delimited"),
)
def test_archive_route_keeps_the_byte_cap_after_the_redirects(
    final: _FakeSocket, most_read: int
) -> None:
    route = _Route(_redirect(_HOP1), _redirect(_HOP2), final)
    with pytest.raises(RemoteCoverError, match="^remote cover is oversized$"):
        route.fetch()
    assert final.body_bytes_read <= most_read


@pytest.mark.parametrize(
    ("final", "message"),
    (
        (
            _FakeSocket(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 4\r\n\r\n",
                b"html",
            ),
            "^remote cover format is unsupported$",
        ),
        (
            _FakeSocket(
                b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n"
                b"Content-Encoding: gzip\r\nContent-Length: 4\r\n\r\n",
                b"gzip",
            ),
            "^remote cover encoding is unsupported$",
        ),
        (
            _FakeSocket(
                b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 4\r\n\r\n",
                b"busy",
            ),
            "^remote cover unavailable$",
        ),
        (
            _FakeSocket(
                b"HTTP/1.1 404 Not Found\r\nContent-Length: 4\r\n\r\n", b"none"
            ),
            "^remote cover unavailable$",
        ),
    ),
    ids=("html", "gzip", "status_503", "status_404"),
)
def test_archive_route_final_answer_keeps_the_existing_admission(
    final: _FakeSocket, message: str
) -> None:
    route = _Route(_redirect(_HOP1), _redirect(_HOP2), final)
    with pytest.raises(RemoteCoverError, match=message):
        route.fetch()
    assert final.body_bytes_read == 0


class _TrickleSocket(_FakeSocket):
    """A datanode that sends one header byte at a time, forever."""

    def recv(self, maximum: int) -> bytes:
        time.sleep(0.01)
        return b"X"


@pytest.mark.parametrize(
    "stall",
    ("headers", "body", "trickle"),
    ids=("stalls_on_headers", "stalls_mid_body", "trickles_header_bytes"),
)
def test_archive_route_datanode_stall_is_a_timeout_inside_the_window(
    stall: str,
) -> None:
    client, peer = socket.socketpair()
    try:
        if stall == "body":
            peer.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n"
                b"Content-Length: 9\r\n\r\ncover"
            )
        final: object = (
            _TrickleSocket(b"") if stall == "trickle" else _PinnedPeerSocket(client)
        )
        started = time.monotonic()
        route = _Route(_redirect(_HOP1), _redirect(_HOP2), final)
        with pytest.raises(RemoteCoverTimeout):
            route.fetch(deadline=CoverDeadline(started + 0.5, started + 0.3))
        assert time.monotonic() - started < 1.5
        assert route.connects == 3
    finally:
        client.close()
        peer.close()


class _SlowExitSocket(_FakeSocket):
    """Spend the attempt window while hop 1's connection closes."""

    def __exit__(self, *_args: object) -> None:
        time.sleep(0.35)


def test_archive_route_rechecks_the_deadline_before_each_hop() -> None:
    first = _SlowExitSocket(
        b"HTTP/1.1 302 Found\r\nLocation: "
        + _HOP1.encode("ascii")
        + b"\r\nContent-Length: 0\r\n\r\n"
    )
    started = time.monotonic()
    route = _Route(first, _image())
    with pytest.raises(RemoteCoverTimeout):
        route.fetch(deadline=CoverDeadline(started + 10, started + 0.3))
    assert (route.connects, route.lookups) == (1, [_COVERS_HOST])


@pytest.mark.parametrize(
    "url",
    (
        "https://example.com/cover.png",
        "https://covers.openlibrary.org/b/id/7-L.jpg",
        "https://covers.openlibrary.org/b/id/7-L.jpg?default=true",
        "https://covers.openlibrary.org/b/id/7-M.jpg?default=false",
        "https://covers.openlibrary.org/b/isbn/7-L.jpg?default=false",
        "https://covers.openlibrary.org/b/id/0-L.jpg?default=false",
        "https://covers.openlibrary.org/b/id/07-L.jpg?default=false",
        "https://covers.openlibrary.org/b/id/2147483648-L.jpg?default=false",
        "http://covers.openlibrary.org/b/id/7-L.jpg?default=false",
        "https://covers.openlibrary.org:443/b/id/7-L.jpg?default=false",
        _COVER_URL + "\n",
        _HOP1,
    ),
    ids=(
        "other_origin",
        "no_query",
        "other_query",
        "medium_size",
        "isbn_path",
        "zero_id",
        "leading_zero",
        "id_above_int32",
        "http",
        "explicit_port",
        "trailing_line_feed",
        "archive_org_first",
    ),
)
def test_archive_mode_refuses_a_non_constructed_url_before_any_lookup(
    url: str,
) -> None:
    route = _Route(_image())
    with pytest.raises(RemoteCoverError, match="^unsafe remote cover$"):
        route.fetch(url)
    assert (route.connects, route.lookups) == (0, [])


@pytest.mark.parametrize(
    "deadline", (None, "float"), ids=("no_deadline", "float_deadline")
)
def test_archive_mode_requires_a_cover_deadline(deadline: str | None) -> None:
    route = _Route(_image())
    with (
        patch("socket.getaddrinfo", side_effect=route._lookup),
        patch("socket.create_connection", side_effect=route._connect),
        pytest.raises(RemoteCoverError, match="^remote cover request is invalid$"),
    ):
        fetch_remote_cover(
            _COVER_URL,
            deadline=None if deadline is None else time.monotonic() + 10,
            redirects=CoverRedirects.OPEN_LIBRARY_ARCHIVE,
        )
    assert (route.connects, route.lookups) == (0, [])


def test_the_largest_cover_id_is_admitted_and_bound() -> None:
    hop1 = "https://archive.org/download/a/b.zip/2147483647-L.jpg"
    hop2 = (
        "https://ia1.us.archive.org/view_archive.php"
        "?archive=/1/items/a/b.zip&file=2147483647-L.jpg"
    )
    route = _Route(_redirect(hop1), _redirect(hop2), _image())
    assert (
        route.fetch(
            "https://covers.openlibrary.org/b/id/2147483647-L.jpg?default=false"
        ).url
        == hop2
    )


def test_redirect_modes_are_a_closed_vocabulary() -> None:
    # Hardcover's bounded generic redirects are gone (#1380): no provider
    # follows any redirect but Open Library's fixed archive route.
    assert {value.value for value in CoverRedirects} == {
        "none",
        "open_library_archive",
    }
    with patch("socket.getaddrinfo") as lookup:
        for value in ("open_library_archive", "public_https", True):
            with pytest.raises(
                RemoteCoverError, match="^remote cover request is invalid$"
            ):
                fetch_remote_cover(_COVER_URL, redirects=value)
    lookup.assert_not_called()


def test_no_mode_but_the_archive_route_follows_a_same_host_redirect() -> None:
    redirect = _FakeSocket(
        b"HTTP/1.1 302 Found\r\nLocation: /final.png\r\nContent-Length: 0\r\n\r\n"
    )
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("socket.create_connection", side_effect=[redirect, _image()]) as connect,
        patch(
            "ssl.create_default_context",
            return_value=_FakeContext("assets.hardcover.app"),
        ),
        pytest.raises(RemoteCoverError, match="^remote cover unavailable$"),
    ):
        fetch_remote_cover(
            "https://assets.hardcover.app/cover.png",
            redirects=CoverRedirects.NONE,
            user_agent=_AGENT,
        )
    connect.assert_called_once()
