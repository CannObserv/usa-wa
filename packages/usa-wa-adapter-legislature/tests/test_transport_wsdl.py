"""The vendored WSDLs (#563): the offline parse's binding, and the drift check that keeps it
honest against the live service.

Drift is keyed on the **parsed binding** — zeep's operation and type signatures — never on
the document's text (ARCHITECTURE rule 2): a whitespace or line-ending change upstream is
not drift; a type or operation change is.

A wire the vendored binding rejects (an element it has never seen) is re-parsed through the
live binding, never leniently: zeep's non-strict parse nulls every field after an unknown
mid-sequence element, which would publish wrong rows rather than fail.
"""

import logging
from pathlib import Path
from typing import Any

import pytest
import yaml
from lxml import etree
from zeep.exceptions import XMLParseError

from usa_wa_adapter_legislature.transport import (
    WSLClient,
    binding_drift,
    vendored_wsdl,
)

CASSETTE_DIR = Path(__file__).parent / "cassettes"
XSD = "http://www.w3.org/2001/XMLSchema"


def _wsdl_cassettes() -> list[tuple[str, str]]:
    """(service, cassette) for every cassette that recorded a live ``?wsdl`` GET."""
    found = []
    for path in sorted(CASSETTE_DIR.glob("*.yaml")):
        for interaction in yaml.safe_load(path.read_text())["interactions"]:
            uri = interaction["request"]["uri"]
            if interaction["request"]["method"] == "GET" and uri.endswith("?wsdl"):
                found.append((uri.rsplit("/", 1)[1].split(".", 1)[0], path.name))
    return found


def _loaded(client: WSLClient) -> WSLClient:
    client._ensure_client()
    return client


def _drop_last_field(service: str, complex_type: str, dest: Path) -> Path:
    """A copy of the vendored WSDL with one field removed from ``complex_type`` — a real
    binding change, the shape a re-versioned upstream type would take."""
    tree = etree.parse(str(vendored_wsdl(service)))
    [sequence] = tree.xpath(
        f"//xsd:complexType[@name='{complex_type}']/xsd:sequence", namespaces={"xsd": XSD}
    )
    sequence.remove(sequence[-1])
    tree.write(str(dest), xml_declaration=True, encoding="utf-8")
    return dest


def _sponsors_envelope() -> bytes:
    interactions = yaml.safe_load(
        (CASSETTE_DIR / "sponsor_service_get_sponsors_2025-26.yaml").read_text()
    )["interactions"]
    [post] = [i for i in interactions if i["request"]["method"] == "POST"]
    return post["response"]["body"]["string"].encode()


def _base_type_gains_a_field(dest: Path) -> tuple[Path, bytes]:
    """WSL's likeliest evolution: a field appended to the ``LegislativeEntity`` base type.
    In a derived ``Member`` it lands *mid*-sequence — after the base fields, before
    ``Party`` — which is the case a lenient parse gets silently wrong. Returns the "live"
    WSDL that knows the field, and a sponsors envelope that carries it."""
    tree = etree.parse(str(vendored_wsdl("SponsorService")))
    [sequence] = tree.xpath(
        "//xsd:complexType[@name='LegislativeEntity']/xsd:sequence", namespaces={"xsd": XSD}
    )
    added = etree.SubElement(sequence, f"{{{XSD}}}element")
    added.attrib.update({"minOccurs": "0", "maxOccurs": "1", "name": "Nick", "type": "s:string"})
    tree.write(str(dest), xml_declaration=True, encoding="utf-8")
    return dest, _sponsors_envelope().replace(b"<Party>", b"<Nick>n</Nick><Party>")


def test_every_service_the_seam_parses_is_vendored() -> None:
    for service in ("CommitteeService", "CommitteeMeetingService", "SponsorService"):
        assert vendored_wsdl(service).is_file()


def test_an_unvendored_service_refuses_rather_than_falling_back_live() -> None:
    with pytest.raises(FileNotFoundError):
        WSLClient.offline("LegislationService")


@pytest.mark.parametrize(("service", "cassette"), _wsdl_cassettes())
def test_each_recorded_live_wsdl_binds_as_the_vendored_one(
    wsl_vcr: Any, service: str, cassette: str
) -> None:
    """The cassettes' recorded ``?wsdl`` bodies and the vendored files are one fact: the
    replay suite exercises the binding the offline parse uses."""
    with wsl_vcr.use_cassette(cassette):
        live = _loaded(WSLClient(service)).binding_fingerprint()
    assert live is not None
    assert live == _loaded(WSLClient.offline(service)).binding_fingerprint()


@pytest.mark.usefixtures("no_network")
def test_fingerprint_is_none_until_the_binding_is_loaded_and_never_fetches() -> None:
    assert WSLClient("SponsorService").binding_fingerprint() is None


@pytest.mark.usefixtures("no_network")
def test_a_text_only_change_is_not_drift(tmp_path: Path) -> None:
    source = vendored_wsdl("CommitteeService").read_bytes()
    reformatted = tmp_path / "CommitteeService.wsdl"
    reformatted.write_bytes(source.replace(b"\r\n", b"\n").replace(b"><", b">\n    <"))
    assert reformatted.read_bytes() != source

    live = _loaded(WSLClient("CommitteeService", wsdl=reformatted))
    assert binding_drift([live]) == {"wsdl_drift": [], "wsdl_unchecked": []}


@pytest.mark.usefixtures("no_network")
def test_a_binding_change_is_drift(tmp_path: Path) -> None:
    changed = _drop_last_field(
        "CommitteeService", "LegislativeEntity", tmp_path / "CommitteeService.wsdl"
    )
    live = _loaded(WSLClient("CommitteeService", wsdl=changed))
    assert binding_drift([live]) == {"wsdl_drift": ["CommitteeService"], "wsdl_unchecked": []}


def test_a_client_that_never_loaded_its_wsdl_is_unchecked_not_drift(wsl_vcr: Any) -> None:
    """An outage that stopped the harvest before its first call leaves nothing to compare:
    unchecked, which the outage's own error already reports."""
    with wsl_vcr.use_cassette("sponsor_service_get_sponsors_2025-26.yaml"):
        sponsors = _loaded(WSLClient("SponsorService"))
    assert binding_drift([sponsors, WSLClient("CommitteeMeetingService")]) == {
        "wsdl_drift": [],
        "wsdl_unchecked": ["CommitteeMeetingService"],
    }


def test_the_cassette_scan_finds_every_vendored_service() -> None:
    """An empty scan would skip the one-fact test rather than fail it."""
    assert {service for service, _ in _wsdl_cassettes()} == {
        "CommitteeService",
        "CommitteeMeetingService",
        "SponsorService",
    }


def test_an_element_the_vendored_binding_lacks_reparses_through_the_live_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    live_wsdl, wire = _base_type_gains_a_field(tmp_path / "SponsorService.wsdl")
    base = WSLClient.offline("SponsorService")._parse_sponsors_sync(_sponsors_envelope())
    client = WSLClient.offline(
        "SponsorService", fallback=WSLClient("SponsorService", wsdl=live_wsdl)
    )

    with caplog.at_level(logging.WARNING):
        first = client._parse_sponsors_sync(wire)
        client._parse_sponsors_sync(wire)

    assert [{k: v for k, v in row.items() if k != "Nick"} for row in first] == base
    assert sum(row["Nick"] == "n" for row in first) == wire.count(b"<Nick>") > 0
    fallbacks = [r for r in caplog.records if r.message == "wsl_parse_live_wsdl_fallback"]
    assert len(fallbacks) == 1, "warned once per client, not once per wire"
    assert fallbacks[0].service == "SponsorService"


def test_without_a_fallback_an_unknown_element_raises_never_parses_leniently(
    tmp_path: Path,
) -> None:
    _, wire = _base_type_gains_a_field(tmp_path / "SponsorService.wsdl")
    with pytest.raises(XMLParseError):
        WSLClient.offline("SponsorService", fallback=None)._parse_sponsors_sync(wire)


@pytest.mark.usefixtures("no_network")
def test_a_wire_the_vendored_binding_parses_never_builds_the_live_fallback() -> None:
    client = WSLClient.offline("SponsorService")
    assert client._parse_sponsors_sync(_sponsors_envelope())
    assert client._fallback is not None
    assert client._fallback.binding_fingerprint() is None
