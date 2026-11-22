"""Optional WebAuthn adapter. The verified public key stays in SQLite."""
import json
import re
from urllib.parse import urlsplit

from .domain import DomainError

FAIL = "Ключ доступа не подтвердил вход. Попробуй снова или выбери другой способ."


def relying_party(origin):
    return urlsplit(origin).hostname


def options(origin, challenge, intent, account=None, excluded=()):
    from webauthn import generate_authentication_options, generate_registration_options, options_to_json
    from webauthn.helpers.structs import (
        AttestationConveyancePreference, AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
        ResidentKeyRequirement, UserVerificationRequirement,
    )

    from webauthn.helpers import base64url_to_bytes

    rp_id = relying_party(origin)
    expected = base64url_to_bytes(challenge)
    required = UserVerificationRequirement.REQUIRED
    descriptors = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(credential)) for credential in excluded]
    if intent == "link":
        result = generate_registration_options(
            rp_id=rp_id, rp_name="Цифровой деканат", user_name=account["username"],
            user_id=account["id"].encode("ascii"), user_display_name=account["username"],
            challenge=expected, timeout=60_000, exclude_credentials=descriptors,
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED, user_verification=required),
        )
    else:
        result = generate_authentication_options(
            rp_id=rp_id, challenge=expected, timeout=60_000, user_verification=required,
            allow_credentials=descriptors if intent == "confirm" else None,
        )
    return json.loads(options_to_json(result))


def registration(origin, row, credential):
    from webauthn import verify_registration_response
    from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
    from webauthn.helpers.exceptions import WebAuthnException

    try:
        result = verify_registration_response(
            credential=credential, expected_challenge=base64url_to_bytes(row["proof"]),
            expected_rp_id=relying_party(origin), expected_origin=origin, require_user_verification=True,
        )
        return bytes_to_base64url(result.credential_id), result.credential_public_key, result.sign_count
    except (WebAuthnException, ValueError, TypeError, KeyError):
        raise DomainError(403, FAIL) from None


def authentication(origin, row, credential, key):
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
    from webauthn.helpers.exceptions import WebAuthnException

    response = credential.get("response")
    handle = response.get("userHandle") if isinstance(response, dict) else None
    if (handle is not None or row["intent"] == "login") and handle != bytes_to_base64url(key["account_id"].encode("ascii")):
        raise DomainError(403, FAIL)
    try:
        result = verify_authentication_response(
            credential=credential, expected_challenge=base64url_to_bytes(row["proof"]),
            expected_rp_id=relying_party(origin), expected_origin=origin, require_user_verification=True,
            credential_public_key=key["public_key"], credential_current_sign_count=key["sign_count"],
        )
        if bytes_to_base64url(result.credential_id) != key["credential_id"]:
            raise DomainError(403, FAIL)
        return result.new_sign_count
    except (WebAuthnException, ValueError, TypeError, KeyError):
        raise DomainError(403, FAIL) from None


def payload(raw):
    try:
        credential = json.loads(raw)
        if (not isinstance(credential, dict) or not isinstance(credential.get("id"), str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,2048}", credential["id"])
                or credential.get("rawId") != credential["id"]
                or credential.get("type") != "public-key"
                or not isinstance(credential.get("response"), dict)):
            raise ValueError
        return credential
    except (ValueError, RecursionError):
        raise DomainError(422, FAIL) from None
