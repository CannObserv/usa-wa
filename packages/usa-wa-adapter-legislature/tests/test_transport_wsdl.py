"""The vendored WSDLs (#563): the offline parse's binding, and the drift check that keeps it
honest against the live service.

Drift is keyed on the **parsed binding** — zeep's operation and type signatures — never on
the document's text (ARCHITECTURE rule 2): a whitespace or line-ending change upstream is
not drift; a type or operation change is.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml
from lxml import etree

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
