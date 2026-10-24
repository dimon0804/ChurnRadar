"""SAML 2.0 Service Provider: SP-initiated вход (HTTP-Redirect) и ACS (HTTP-POST).

Подпись XML проверяет signxml (lxml + cryptography, без нативного xmlsec). Всё остальное
читается только из того фрагмента документа, который реально покрыт подписью: signxml
возвращает его отдельно, и именно с ним мы работаем. Это закрывает атаки с подменой
обёртки (XML Signature Wrapping). Дополнительно: ровно одна ассерция в ответе, Issuer,
Audience, Recipient, окна NotBefore/NotOnOrAfter, InResponseTo; повтор ассерции ловит
вызывающий код по ID в Redis.
"""

from __future__ import annotations

import base64
import secrets
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from lxml import etree
from signxml import SignatureConfiguration, XMLVerifier
from signxml.exceptions import SignXMLException

from .config import Settings

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "md": "urn:oasis:names:tc:SAML:2.0:metadata",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
}
BINDING_REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
BINDING_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
STATUS_SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
CM_BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
MAX_RESPONSE_BYTES = 256 * 1024


class SamlError(Exception):
    pass


def _q(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


def parse_xml(data: bytes) -> etree._Element:
    """Parse untrusted XML: no DTD, no entities, no network, no comments."""
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise SamlError("DTD is not allowed")
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, remove_comments=True, huge_tree=False
    )
    try:
        root = etree.fromstring(data, parser=parser)  # noqa: S320 -- парсер выше без DTD, сущностей и сети
    except etree.XMLSyntaxError as exc:
        raise SamlError(f"malformed XML: {exc}") from exc
    if root is None:
        raise SamlError("empty XML")
    return root


def _text(el: etree._Element | None) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def parse_instant(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip().replace("Z", "+00:00")
    # xs:dateTime допускает больше 6 знаков после запятой, fromisoformat — нет
    if "." in raw:
        head, _, rest = raw.partition(".")
        digits = "".join(ch for ch in rest if ch.isdigit())
        tz = rest[len(digits) :]
        raw = f"{head}.{digits[:6].ljust(6, '0')}{tz}"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SamlError(f"bad timestamp {value!r}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def instant(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------- IdP metadata


@dataclass
class IdpMetadata:
    entity_id: str
    sso_url: str
    certificates: list[x509.Certificate] = field(default_factory=list)


def parse_idp_metadata(xml: bytes, internal_origin: str = "", public_origin: str = "") -> IdpMetadata:
    root = parse_xml(xml)
    entity = root if root.tag == _q("md", "EntityDescriptor") else root.find(".//md:EntityDescriptor", NS)
    if entity is None:
        raise SamlError("no EntityDescriptor in IdP metadata")
    idp = entity.find("md:IDPSSODescriptor", NS)
    if idp is None:
        raise SamlError("no IDPSSODescriptor in IdP metadata")

    sso_url = ""
    for sso in idp.findall("md:SingleSignOnService", NS):
        if sso.get("Binding") == BINDING_REDIRECT:
            sso_url = sso.get("Location", "")
            break
    if not sso_url:
        raise SamlError("IdP has no HTTP-Redirect SingleSignOnService")
    if internal_origin and public_origin and sso_url.startswith(internal_origin + "/"):
        sso_url = public_origin + sso_url[len(internal_origin) :]

    certs: list[x509.Certificate] = []
    for kd in idp.findall("md:KeyDescriptor", NS):
        if kd.get("use") not in (None, "signing"):
            continue
        for cert_el in kd.findall(".//ds:X509Certificate", NS):
            der = base64.b64decode("".join(_text(cert_el).split()))
            certs.append(x509.load_der_x509_certificate(der))
    if not certs:
        raise SamlError("IdP metadata has no signing certificate")
    entity_id = entity.get("entityID", "")
    if not entity_id:
        raise SamlError("IdP metadata has no entityID")
    return IdpMetadata(entity_id=entity_id, sso_url=sso_url, certificates=certs)


# ---------------------------------------------------------------------------- SP side


def sp_metadata(entity_id: str, acs_url: str) -> bytes:
    md = NS["md"]
    root = etree.Element(_q("md", "EntityDescriptor"), nsmap={"md": md}, entityID=entity_id)
    sp = etree.SubElement(
        root,
        _q("md", "SPSSODescriptor"),
        AuthnRequestsSigned="false",
        WantAssertionsSigned="true",
        protocolSupportEnumeration=NS["samlp"],
    )
    for fmt in (
        "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
        "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified",
    ):
        etree.SubElement(sp, _q("md", "NameIDFormat")).text = fmt
    etree.SubElement(
        sp, _q("md", "AssertionConsumerService"), Binding=BINDING_POST, Location=acs_url, index="0", isDefault="true"
    )
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def new_request_id() -> str:
    # xs:ID — NCName, с цифры начинаться не может
    return "_" + secrets.token_hex(20)


def authn_request_url(idp: IdpMetadata, entity_id: str, acs_url: str, request_id: str, now: datetime) -> str:
    req = etree.Element(
        _q("samlp", "AuthnRequest"),
        nsmap={"samlp": NS["samlp"], "saml": NS["saml"]},
        ID=request_id,
        Version="2.0",
        IssueInstant=instant(now),
        Destination=idp.sso_url,
        AssertionConsumerServiceURL=acs_url,
        ProtocolBinding=BINDING_POST,
    )
    etree.SubElement(req, _q("saml", "Issuer")).text = entity_id
    etree.SubElement(req, _q("samlp", "NameIDPolicy"), AllowCreate="true")
    raw = etree.tostring(req)
    deflater = zlib.compressobj(9, zlib.DEFLATED, -15)
    deflated = deflater.compress(raw) + deflater.flush()
    query = urlencode({"SAMLRequest": base64.b64encode(deflated).decode()})
    return f"{idp.sso_url}{'&' if '?' in idp.sso_url else '?'}{query}"


@dataclass
class SamlAssertion:
    assertion_id: str
    subject: str
    in_response_to: str
    attributes: dict[str, list[str]]
    expires_at: datetime

    def first(self, *names: str) -> str:
        for name in names:
            values = self.attributes.get(name)
            if values and values[0]:
                return values[0]
        return ""


def _verify(data: bytes, idp: IdpMetadata, location: str) -> etree._Element:
    last: Exception | None = None
    config = SignatureConfiguration(location=location, expect_references=1)
    for cert in idp.certificates:
        try:
            result = XMLVerifier().verify(data, x509_cert=cert, expect_config=config)
        except (SignXMLException, InvalidSignature, etree.LxmlError) as exc:
            last = exc
            continue
        return result.signed_xml
    raise SamlError(f"signature check failed: {last}")


def _signed_assertion(raw: bytes, response: etree._Element, idp: IdpMetadata) -> etree._Element:
    """The assertion as returned by the signature check: the only part of the document we trust."""
    if response.findall(".//saml:EncryptedAssertion", NS):
        raise SamlError("encrypted assertions are not supported")
    all_assertions = response.findall(".//saml:Assertion", NS)
    direct = response.findall("saml:Assertion", NS)
    if len(all_assertions) != 1 or len(direct) != 1:
        raise SamlError("response must contain exactly one assertion")
    assertion = direct[0]

    if response.find("ds:Signature", NS) is not None:
        signed = _verify(raw, idp, "./")
        if signed.tag != _q("samlp", "Response") or signed.get("ID") != response.get("ID"):
            raise SamlError("signature does not cover the response")
        trusted = signed.findall("saml:Assertion", NS)
        if len(trusted) != 1:
            raise SamlError("signed response must contain exactly one assertion")
        return trusted[0]

    if assertion.find("ds:Signature", NS) is None:
        raise SamlError("neither response nor assertion is signed")
    signed = _verify(raw, idp, f"./{{{NS['saml']}}}Assertion/")
    if signed.tag != _q("saml", "Assertion") or signed.get("ID") != assertion.get("ID"):
        raise SamlError("signature does not cover the assertion")
    return signed


def parse_response(
    saml_response: str,
    *,
    idp: IdpMetadata,
    sp_entity_id: str,
    acs_url: str,
    now: datetime | None = None,
    skew_seconds: int = 90,
) -> SamlAssertion:
    now = now or datetime.now(timezone.utc)
    skew = timedelta(seconds=skew_seconds)
    try:
        raw = base64.b64decode("".join(saml_response.split()), validate=True)
    except (ValueError, TypeError) as exc:
        raise SamlError("SAMLResponse is not base64") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SamlError("SAMLResponse is too large")

    response = parse_xml(raw)
    if response.tag != _q("samlp", "Response") or response.get("Version") != "2.0":
        raise SamlError("not a SAML 2.0 Response")
    destination = response.get("Destination")
    if destination and destination != acs_url:
        raise SamlError(f"wrong Destination {destination!r}")
    status = response.find("samlp:Status/samlp:StatusCode", NS)
    if status is None or status.get("Value") != STATUS_SUCCESS:
        raise SamlError(f"IdP returned status {status.get('Value') if status is not None else 'none'}")

    assertion = _signed_assertion(raw, response, idp)

    issuer = _text(assertion.find("saml:Issuer", NS))
    if issuer != idp.entity_id:
        raise SamlError(f"unexpected Issuer {issuer!r}")

    subject_el = assertion.find("saml:Subject", NS)
    name_id = _text(subject_el.find("saml:NameID", NS)) if subject_el is not None else ""
    if not name_id:
        raise SamlError("assertion has no NameID")

    in_response_to = ""
    expires: list[datetime] = []
    bearer_ok = False
    for sc in subject_el.findall("saml:SubjectConfirmation", NS):
        if sc.get("Method") != CM_BEARER:
            continue
        data = sc.find("saml:SubjectConfirmationData", NS)
        if data is None:
            continue
        recipient = data.get("Recipient")
        not_after = parse_instant(data.get("NotOnOrAfter"))
        if recipient != acs_url or not_after is None or now >= not_after + skew:
            continue
        in_response_to = data.get("InResponseTo", "")
        expires.append(not_after)
        bearer_ok = True
        break
    if not bearer_ok:
        raise SamlError("no valid bearer SubjectConfirmation (Recipient / NotOnOrAfter)")

    conditions = assertion.find("saml:Conditions", NS)
    if conditions is None:
        raise SamlError("assertion has no Conditions")
    not_before = parse_instant(conditions.get("NotBefore"))
    not_on_or_after = parse_instant(conditions.get("NotOnOrAfter"))
    if not_before and now + skew < not_before:
        raise SamlError("assertion is not yet valid")
    if not_on_or_after:
        if now >= not_on_or_after + skew:
            raise SamlError("assertion has expired")
        expires.append(not_on_or_after)
    restrictions = conditions.findall("saml:AudienceRestriction", NS)
    if not restrictions:
        raise SamlError("assertion has no AudienceRestriction")
    for restriction in restrictions:
        audiences = {_text(a) for a in restriction.findall("saml:Audience", NS)}
        if sp_entity_id not in audiences:
            raise SamlError(f"assertion is meant for {sorted(audiences)}")

    # без InResponseTo это незапрошенный (IdP-initiated) ответ — такие не принимаем
    if not in_response_to:
        raise SamlError("unsolicited response (no InResponseTo)")
    outer = response.get("InResponseTo")
    if outer and outer != in_response_to:
        raise SamlError("InResponseTo mismatch")

    attributes: dict[str, list[str]] = {}
    for attr in assertion.findall("saml:AttributeStatement/saml:Attribute", NS):
        name = attr.get("Name", "")
        values = [_text(v) for v in attr.findall("saml:AttributeValue", NS)]
        attributes.setdefault(name, []).extend(v for v in values if v)
        friendly = attr.get("FriendlyName")
        if friendly and friendly not in attributes:
            attributes[friendly] = attributes[name]

    assertion_id = assertion.get("ID", "")
    if not assertion_id:
        raise SamlError("assertion has no ID")
    return SamlAssertion(
        assertion_id=assertion_id,
        subject=name_id,
        in_response_to=in_response_to,
        attributes=attributes,
        expires_at=min(expires),
    )


# ---------------------------------------------------------------------------- runtime client

USERNAME_ATTRS = (
    "http://schemas.goauthentik.io/2021/02/saml/username",
    "username",
    "uid",
    "urn:oid:0.9.2342.19200300.100.1.1",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "email",
)
NAME_ATTRS = (
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
    "displayName",
    "urn:oid:2.16.840.1.113730.3.1.241",
    "name",
)


class SamlClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.s = settings
        self.http = http
        self._idp: tuple[float, IdpMetadata | None] = (0.0, None)

    def _origins(self) -> tuple[str, str]:
        if not (self.s.saml_idp_metadata_url and self.s.saml_idp_public_url):
            return "", ""
        internal = urlsplit(self.s.saml_idp_metadata_url)
        public = urlsplit(self.s.saml_idp_public_url)
        return f"{internal.scheme}://{internal.netloc}", f"{public.scheme}://{public.netloc}"

    async def _load(self) -> IdpMetadata:
        if self.s.saml_idp_metadata_xml.strip():
            return parse_idp_metadata(self.s.saml_idp_metadata_xml.encode())
        if self.s.saml_idp_metadata_file:
            return parse_idp_metadata(Path(self.s.saml_idp_metadata_file).read_bytes())
        if not self.s.saml_idp_metadata_url:
            raise SamlError("IdP metadata is not configured")
        internal_origin, public_origin = self._origins()
        headers = {}
        if public_origin and public_origin != internal_origin:
            # Authentik собирает адрес SSO из Host запроса: просим сразу публичный
            headers["Host"] = urlsplit(self.s.saml_idp_public_url).netloc
        # Authentik отдаёт метаданные приложения через редирект на /api/v3/providers/saml/<id>/metadata/
        resp = await self.http.get(self.s.saml_idp_metadata_url, headers=headers, timeout=2, follow_redirects=True)
        if resp.status_code != 200:
            raise SamlError(f"IdP metadata: HTTP {resp.status_code}")
        return parse_idp_metadata(resp.content, internal_origin, public_origin)

    async def idp(self) -> IdpMetadata | None:
        if not self.s.saml_enabled:
            return None
        fetched_at, idp = self._idp
        ttl = 600 if idp else 15
        if time.monotonic() - fetched_at < ttl:
            return idp
        try:
            idp = await self._load()
        except (SamlError, httpx.HTTPError, OSError, ValueError):
            idp = None
        self._idp = (time.monotonic(), idp)
        return idp

    async def available(self) -> bool:
        return await self.idp() is not None

    def metadata(self) -> bytes:
        return sp_metadata(self.s.saml_entity_id, self.s.saml_acs_url)

    async def login_url(self, request_id: str) -> str:
        idp = await self.idp()
        if idp is None:
            raise SamlError("IdP unavailable")
        return authn_request_url(
            idp, self.s.saml_entity_id, self.s.saml_acs_url, request_id, datetime.now(timezone.utc)
        )

    async def parse(self, saml_response: str) -> SamlAssertion:
        idp = await self.idp()
        if idp is None:
            raise SamlError("IdP unavailable")
        try:
            return parse_response(
                saml_response,
                idp=idp,
                sp_entity_id=self.s.saml_entity_id,
                acs_url=self.s.saml_acs_url,
                skew_seconds=self.s.saml_clock_skew_seconds,
            )
        except SamlError as exc:
            # IdP мог сменить ключ: перечитываем метаданные один раз и пробуем снова
            if "signature" not in str(exc):
                raise
            self._idp = (0.0, None)
            fresh = await self.idp()
            if fresh is None or fresh.certificates == idp.certificates:
                raise
            return parse_response(
                saml_response,
                idp=fresh,
                sp_entity_id=self.s.saml_entity_id,
                acs_url=self.s.saml_acs_url,
                skew_seconds=self.s.saml_clock_skew_seconds,
            )
