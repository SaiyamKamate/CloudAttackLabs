"""targets/url_policy.py: only approved local lab addresses can be registered."""
import pytest

from app.targets.url_policy import UrlRefused, check_pinned, check_url

CIDRS = ("127.0.0.0/8",)
PORTS = (8090,)


def check(url, cidrs=CIDRS, ports=PORTS):
    return check_url(url, allowed_cidrs=cidrs, allowed_ports=ports)


@pytest.mark.parametrize(
    "url, base",
    [
        ("http://127.0.0.1:8090", "http://127.0.0.1:8090"),
        ("http://127.0.0.1:8090/", "http://127.0.0.1:8090"),
        ("http://localhost:8090", "http://127.0.0.1:8090"),
        ("HTTP://LOCALHOST:8090", "http://127.0.0.1:8090"),
        ("https://127.0.0.2:8090", "https://127.0.0.2:8090"),
        ("  http://127.0.0.1:8090  ", "http://127.0.0.1:8090"),
    ],
)
def test_accepts_local_lab_urls(url, base):
    approved = check(url)
    assert approved.base_url == base
    assert approved.port == 8090


@pytest.mark.parametrize(
    "url, reason",
    [
        ("http://example.com:8090", "host name"),
        ("https://shopcloud.local:8090", "host name"),
        ("http://8.8.8.8:8090", "approved lab ranges"),
        ("http://10.0.0.5:8090", "approved lab ranges"),
        ("http://169.254.169.254:8090", "link-local"),
        ("http://169.254.169.254", "link-local"),
        ("http://[::ffff:169.254.169.254]:8090", "link-local"),
        ("http://[fe80::1]:8090", "link-local"),
        ("http://[fd00:ec2::254]:8090", "metadata"),
        ("http://100.100.100.200:8090", "metadata"),
        ("http://0.0.0.0:8090", "unspecified"),
        ("http://[::]:8090", "unspecified"),
        ("http://224.0.0.1:8090", "multicast"),
        ("http://240.0.0.1:8090", "reserved"),
        ("http://127.0.0.1:8000", "CloudAttackLab itself"),
        ("http://127.0.0.1:5432", "CloudAttackLab itself"),
        ("http://127.0.0.1:5173", "CloudAttackLab itself"),
        ("http://127.0.0.1:3100", "CloudAttackLab itself"),
        ("http://127.0.0.1:8081", "not an approved target port"),
        ("http://127.0.0.1", "port explicitly"),
        ("http://user:pw@127.0.0.1:8090", "user name or password"),
        ("http://evil.com@127.0.0.1:8090", "user name or password"),
        ("ftp://127.0.0.1:8090", "only http"),
        ("file:///etc/passwd", "only http"),
        ("http://127.0.0.1:8090/admin", "base URL only"),
        ("http://127.0.0.1:8090/?x=1", "base URL only"),
        ("http://127.0.0.1:8090/#x", "base URL only"),
        ("http://127.0.0.1:99999", "not a valid URL"),
        ("http://127.0.0.1:8090\r\nHost: x", "control characters"),
        ("", "empty"),
    ],
)
def test_refuses_everything_else(url, reason):
    with pytest.raises(UrlRefused) as exc:
        check(url)
    assert reason in str(exc.value)


def test_a_private_lab_subnet_can_be_approved():
    approved = check("http://10.20.0.5:8090", cidrs=("127.0.0.0/8", "10.20.0.0/24"))
    assert approved.ip == "10.20.0.5"


def test_allowlisting_metadata_does_not_open_it():
    with pytest.raises(UrlRefused):
        check("http://169.254.169.254:8090", cidrs=("169.254.0.0/16",))


def test_platform_ports_stay_refused_even_if_allowlisted():
    with pytest.raises(UrlRefused):
        check("http://127.0.0.1:8000", ports=(8000, 8090))


def test_pinned_check_detects_a_changed_url():
    check_pinned("http://127.0.0.1:8090", "127.0.0.1", 8090, allowed_cidrs=CIDRS, allowed_ports=PORTS)
    with pytest.raises(UrlRefused):
        check_pinned("http://127.0.0.1:8090", "127.0.0.2", 8090, allowed_cidrs=CIDRS, allowed_ports=PORTS)
    # The policy was tightened after registration: the stored target is refused now.
    with pytest.raises(UrlRefused):
        check_pinned("http://127.0.0.1:8090", "127.0.0.1", 8090, allowed_cidrs=CIDRS, allowed_ports=(9000,))
