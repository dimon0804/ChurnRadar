from __future__ import annotations

import base64
import zlib
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner
from signxml.algorithms import CanonicalizationMethod

from app.config import Settings
from app.saml import NS, SamlError, parse_idp_metadata, parse_response

from .conftest import ORIGIN

IDP_ENTITY = "churn-radar-authentik"
SP_ENTITY = "http://localhost:8080/api/v1/auth/saml/metadata"
ACS = "http://localhost:8080/api/v1/auth/saml/acs"
SSO_URL = "http://localhost:9000/application/saml/churn-radar-saml/sso/binding/redirect/"
GROUPS = "http://schemas.xmlsoap.org/claims/Group"
USERNAME = "http://schemas.goauthentik.io/2021/02/saml/username"


def make_keypair(cn: str):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return key, cert


IDP_KEY, IDP_CERT = make_keypair("authentik Self-signed Certificate")
EVIL_KEY, EVIL_CERT = make_keypair("evil")


def pem(obj) -> bytes:
    if isinstance(obj, x509.Certificate):
        return obj.public_bytes(serialization.Encoding.PEM)
    return obj.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def idp_metadata(cert=IDP_CERT, sso_url=SSO_URL) -> str:
    der = base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode()
    return f"""<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
    entityID="{IDP_ENTITY}">
  <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:KeyDescriptor use="signing"><ds:KeyInfo><ds:X509Data><ds:X509Certificate>{der}</ds:X509Certificate>
    </ds:X509Data></ds:KeyInfo></md:KeyDescriptor>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="{sso_url}post/"/>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="{sso_url}"/>
  </md:IDPSSODescriptor>
</md:EntityDescriptor>"""


def ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def assertion_xml(
    *,
    request_id: str,
    assertion_id: str = "_a1",
    audience: str = SP_ENTITY,
    recipient: str = ACS,
    issuer: str = IDP_ENTITY,
    now: datetime | None = None,
    lifetime: timedelta = timedelta(minutes=5),
    groups: tuple[str, ...] = ("churn-analysts",),
    username: str = "marketer",
) -> etree._Element:
    now = now or datetime.now(timezone.utc)
    group_values = "".join(f"<saml:AttributeValue>{g}</saml:AttributeValue>" for g in groups)
    xml = f"""<saml:Assertion xmlns:saml="{NS["saml"]}" ID="{assertion_id}" Version="2.0" IssueInstant="{ts(now)}">
  <saml:Issuer>{issuer}</saml:Issuer>
  <ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#" Id="placeholder"/>
  <saml:Subject>
    <saml:NameID Format="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent">ak-hash-{username}</saml:NameID>
    <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
      <saml:SubjectConfirmationData InResponseTo="{request_id}" NotOnOrAfter="{ts(now + lifetime)}"
          Recipient="{recipient}"/>
    </saml:SubjectConfirmation>
  </saml:Subject>
  <saml:Conditions NotBefore="{ts(now - timedelta(seconds=30))}" NotOnOrAfter="{ts(now + lifetime)}">
    <saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction>
  </saml:Conditions>
  <saml:AuthnStatement AuthnInstant="{ts(now)}"><saml:AuthnContext>
    <saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</saml:AuthnContextClassRef>
  </saml:AuthnContext></saml:AuthnStatement>
  <saml:AttributeStatement>
    <saml:Attribute Name="{USERNAME}"><saml:AttributeValue>{username}</saml:AttributeValue></saml:Attribute>
    <saml:Attribute Name="http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name">
      <saml:AttributeValue>Мария Маркетолог</saml:AttributeValue></saml:Attribute>
    <saml:Attribute Name="{GROUPS}">{group_values}</saml:Attribute>
  </saml:AttributeStatement>
</saml:Assertion>"""
    return etree.fromstring(xml.encode())


def sign(el: etree._Element, key=IDP_KEY, cert=IDP_CERT) -> etree._Element:
    signer = XMLSigner(c14n_algorithm=CanonicalizationMethod.EXCLUSIVE_XML_CANONICALIZATION_1_0)
    return signer.sign(el, key=pem(key), cert=pem(cert).decode())


def response_xml(assertions: list[etree._Element], request_id: str, destination: str = ACS) -> bytes:
    resp = etree.fromstring(
        f"""<samlp:Response xmlns:samlp="{NS["samlp"]}" xmlns:saml="{NS["saml"]}" ID="_r1" Version="2.0"
    IssueInstant="{ts(datetime.now(timezone.utc))}" Destination="{destination}" InResponseTo="{request_id}">
  <saml:Issuer>{IDP_ENTITY}</saml:Issuer>
  <samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>
</samlp:Response>""".encode()
    )
    for a in assertions:
        resp.append(etree.fromstring(etree.tostring(a)))
    return etree.tostring(resp)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def signed_response(request_id: str = "_req1", **kwargs) -> str:
    return b64(response_xml([sign(assertion_xml(request_id=request_id, **kwargs))], request_id))


IDP = parse_idp_metadata(idp_metadata().encode())


def parse(saml_response: str, **kwargs):
    return parse_response(saml_response, idp=IDP, sp_entity_id=SP_ENTITY, acs_url=ACS, **kwargs)


# ---------------------------------------------------------------------------- parse_response


def test_metadata_parsing():
    assert IDP.entity_id == IDP_ENTITY
    assert IDP.sso_url == SSO_URL
    assert IDP.certificates == [IDP_CERT]
    # метаданные скачаны по внутреннему адресу: SSO-ссылку для браузера переписываем на публичный
    internal = idp_metadata(sso_url="http://authentik-server:9000/application/saml/x/sso/binding/redirect/")
    md = parse_idp_metadata(internal.encode(), "http://authentik-server:9000", "http://localhost:9000")
    assert md.sso_url == "http://localhost:9000/application/saml/x/sso/binding/redirect/"


def test_valid_signed_assertion_passes():
    result = parse(signed_response())
    assert result.subject == "ak-hash-marketer"
    assert result.in_response_to == "_req1"
    assert result.assertion_id == "_a1"
    assert result.attributes[GROUPS] == ["churn-analysts"]
    assert result.first(USERNAME) == "marketer"


def test_wrong_key_rejected():
    forged = b64(response_xml([sign(assertion_xml(request_id="_req1"), EVIL_KEY, EVIL_CERT)], "_req1"))
    with pytest.raises(SamlError, match="signature"):
        parse(forged)


def test_tampered_assertion_rejected():
    signed = sign(assertion_xml(request_id="_req1", groups=("churn-analysts",)))
    value = signed.find(f".//saml:Attribute[@Name='{GROUPS}']/saml:AttributeValue", NS)
    value.text = "churn-admins"
    with pytest.raises(SamlError, match="signature"):
        parse(b64(response_xml([signed], "_req1")))


def test_unsigned_assertion_rejected():
    el = assertion_xml(request_id="_req1")
    el.remove(el.find("ds:Signature", NS))
    with pytest.raises(SamlError, match="neither"):
        parse(b64(response_xml([el], "_req1")))


def test_foreign_audience_rejected():
    with pytest.raises(SamlError, match="meant for"):
        parse(signed_response(audience="https://other-app.example/saml"))


def test_expired_assertion_rejected():
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    with pytest.raises(SamlError):
        parse(signed_response(now=old))


def test_not_yet_valid_assertion_rejected():
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    with pytest.raises(SamlError, match="not yet valid"):
        parse(signed_response(now=future))


def test_wrong_recipient_rejected():
    with pytest.raises(SamlError, match="SubjectConfirmation"):
        parse(signed_response(recipient="https://evil.example/acs"))


def test_wrong_issuer_rejected():
    with pytest.raises(SamlError, match="Issuer"):
        parse(signed_response(issuer="someone-else"))


def test_unsolicited_response_rejected():
    with pytest.raises(SamlError, match="unsolicited"):
        parse(signed_response(request_id=""))


def test_signature_wrapping_rejected():
    """XSW: the genuine signed assertion is hidden deeper, a forged one sits where the SP looks."""
    genuine = sign(assertion_xml(request_id="_req1"))
    forged = assertion_xml(request_id="_req1", assertion_id="_evil", groups=("churn-admins",))
    forged.remove(forged.find("ds:Signature", NS))
    forged.append(genuine)
    with pytest.raises(SamlError, match="exactly one"):
        parse(b64(response_xml([forged], "_req1")))


def test_doctype_rejected():
    payload = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'
    with pytest.raises(SamlError, match="DTD"):
        parse(b64(payload))


# ---------------------------------------------------------------------------- HTTP flow


@pytest.fixture
def settings() -> Settings:
    return Settings(oidc_enabled=False, saml_enabled=True, saml_idp_metadata_xml=idp_metadata(), log_level="warning")


async def start_login(client, next_url="/customers") -> str:
    resp = await client.get("/api/v1/auth/saml/login", params={"next": next_url})
    assert resp.status_code == 302
    location = urlsplit(resp.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == SSO_URL
    raw = base64.b64decode(parse_qs(location.query)["SAMLRequest"][0])
    request = etree.fromstring(zlib.decompress(raw, -15))
    assert request.get("AssertionConsumerServiceURL") == ACS
    assert request.findtext("saml:Issuer", namespaces=NS) == SP_ENTITY
    return request.get("ID")


async def post_acs(client, saml_response: str):
    return await client.post(
        "/api/v1/auth/saml/acs",
        data={"SAMLResponse": saml_response, "RelayState": ""},
        headers={"Origin": "http://localhost:9000"},
    )


async def test_providers_and_sp_metadata(client):
    body = (await client.get("/api/v1/auth/providers")).json()
    assert body["saml"] == {
        "enabled": True,
        "display_name": "Войти через Authentik (SAML)",
        "login_url": "/api/v1/auth/saml/login",
    }
    md = await client.get("/api/v1/auth/saml/metadata")
    assert md.headers["content-type"].startswith("application/samlmetadata+xml")
    root = etree.fromstring(md.content)
    assert root.get("entityID") == SP_ENTITY
    acs = root.find(".//md:AssertionConsumerService", NS)
    assert acs.get("Location") == ACS


async def test_full_saml_login_creates_session(client, repo):
    request_id = await start_login(client)
    resp = await post_acs(client, signed_response(request_id, groups=("churn-admins", "churn-analysts")))
    assert resp.status_code == 303
    assert resp.headers["location"] == "/customers"
    me = (await client.get("/api/v1/auth/me")).json()
    assert me["auth_method"] == "saml"
    assert me["username"] == "marketer"
    assert me["display_name"] == "Мария Маркетолог"
    assert me["roles"] == ["admin", "analyst"]
    assert repo.logins[-1][:3] == ("marketer", "saml", True)
    # та же сессия, что и при входе по паролю: выход работает обычным способом
    out = await client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})
    assert out.status_code == 204


async def test_replayed_assertion_rejected(client):
    request_id = await start_login(client)
    response = signed_response(request_id)
    assert (await post_acs(client, response)).headers["location"] == "/customers"
    client.cookies.clear()
    replay = await post_acs(client, response)
    assert replay.headers["location"] == "/login?error=sso"
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_replay_guard_blocks_same_assertion_for_new_request(client):
    """Even with a fresh request ID the same assertion ID is not accepted twice."""
    first = await start_login(client)
    assert (await post_acs(client, signed_response(first, assertion_id="_same"))).headers["location"] == "/customers"
    client.cookies.clear()
    second = await start_login(client)
    again = await post_acs(client, signed_response(second, assertion_id="_same"))
    assert again.headers["location"] == "/login?error=sso"


async def test_response_for_unknown_request_rejected(client):
    await start_login(client)
    resp = await post_acs(client, signed_response("_not-ours"))
    assert resp.headers["location"] == "/login?error=sso"


async def test_user_without_groups_refused(client):
    request_id = await start_login(client)
    resp = await post_acs(client, signed_response(request_id, groups=("staff",)))
    assert resp.headers["location"] == "/login?error=forbidden"
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_garbage_post_rejected(client):
    resp = await client.post("/api/v1/auth/saml/acs", content=b"SAMLResponse=%%%")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?error=sso"
