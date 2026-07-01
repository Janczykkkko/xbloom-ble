"""Tests for the xBloom **cloud** subsystem (xbloom_ble.cloud).

No live network: HTTP is monkeypatched. Encryption is exercised against a
locally-generated RSA key so the round-trip is verifiable without the embedded
production key, and the chunking/base64 structure is asserted directly.
"""

from __future__ import annotations

import base64
import json

import pytest

from xbloom_ble import cloud
from xbloom_ble.cloud import (
    RSA_KEY_BYTES,
    RSA_MAX_PLAIN_BLOCK,
    XBloomCloud,
    XBloomCloudError,
    encrypt_form,
    recipe_to_cloud,
)
from xbloom_ble.recipe import Recipe

# cryptography is required for the cloud subsystem; skip cleanly if absent.
crypto = pytest.importorskip("cryptography")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _sample_recipe() -> Recipe:
    return Recipe.from_dict(
        {
            "name": "Test Recipe",
            "dose_g": 15,
            "grind": 63,
            "ratio": 16,  # → grandWater must be 16.0
            "pours": [
                # bloom: spiral + agitation → pattern 3, vibration-before ON (1)
                {"ml": 40, "temp_c": 95, "pattern": "spiral", "agitation": True,
                 "pause_s": 30, "rpm": 90, "flow_ml_s": 3.0},
                # ring → cloud pattern 2 (circular)
                {"ml": 100, "temp_c": 92, "pattern": "ring",
                 "pause_s": 0, "rpm": 100, "flow_ml_s": 3.5},
                # center → cloud pattern 1
                {"ml": 100, "temp_c": 90, "pattern": "center",
                 "pause_s": 5, "rpm": 0, "flow_ml_s": 3.5},
            ],
        }
    )


# ---------------------------------------------------------------------------
# recipe_to_cloud mapping
# ---------------------------------------------------------------------------

def test_recipe_to_cloud_top_level_fields():
    c = recipe_to_cloud(_sample_recipe(), cup_type="xpod")
    assert c["theName"] == "Test Recipe"
    assert c["dose"] == 15.0
    # grandWater is the RATIO, not total water (Σml = 240, but ratio = 16).
    assert c["grandWater"] == 16.0
    assert c["grinderSize"] == 63.0
    # rpm comes from the first pour.
    assert c["rpm"] == 90
    # cup type name → code.
    assert c["cupType"] == cloud.CUP_TYPES["xpod"] == 1


def test_recipe_to_cloud_cup_type_by_name_and_int():
    r = _sample_recipe()
    assert recipe_to_cloud(r, cup_type="tea")["cupType"] == 4
    assert recipe_to_cloud(r, cup_type=2)["cupType"] == 2
    with pytest.raises(XBloomCloudError):
        recipe_to_cloud(r, cup_type="mug")


def test_recipe_to_cloud_grandwater_is_ratio_not_water():
    # A recipe with an explicit ratio different from Σml/dose proves it's the ratio.
    c = recipe_to_cloud(_sample_recipe())
    assert c["grandWater"] == 16.0
    # total water would be 240 — must NOT appear as grandWater.
    assert c["grandWater"] != 240.0


def test_recipe_to_cloud_booleans_are_one_or_two():
    c = recipe_to_cloud(_sample_recipe())
    # Top-level cloud booleans.
    assert c["isSetGrinderSize"] in (1, 2)
    assert c["isEnableBypassWater"] in (1, 2)
    assert c["isShortcuts"] in (1, 2)
    pours = json.loads(c["pourDataJSONStr"])
    for p in pours:
        assert p["isEnableVibrationBefore"] in (1, 2)
        assert p["isEnableVibrationAfter"] in (1, 2)
        # never a python bool / true / false
        assert p["isEnableVibrationBefore"] is not True
        assert p["isEnableVibrationBefore"] is not False


def test_recipe_to_cloud_agitation_maps_to_vibration_before():
    pours = json.loads(recipe_to_cloud(_sample_recipe())["pourDataJSONStr"])
    # First pour has agitation=True → vibration-before ON (1).
    assert pours[0]["isEnableVibrationBefore"] == 1
    # Second pour has no agitation → OFF (2).
    assert pours[1]["isEnableVibrationBefore"] == 2


def test_recipe_to_cloud_pattern_codes():
    pours = json.loads(recipe_to_cloud(_sample_recipe())["pourDataJSONStr"])
    assert pours[0]["pattern"] == 3  # spiral
    assert pours[1]["pattern"] == 2  # ring → circular
    assert pours[2]["pattern"] == 1  # center


def test_recipe_to_cloud_pourdata_is_valid_json_string():
    c = recipe_to_cloud(_sample_recipe())
    # pourDataJSONStr must be a *string*, and valid JSON of a list.
    assert isinstance(c["pourDataJSONStr"], str)
    pours = json.loads(c["pourDataJSONStr"])
    assert isinstance(pours, list) and len(pours) == 3
    for p in pours:
        assert set(p) >= {
            "theName", "volume", "temperature", "flowRate", "pattern",
            "pausing", "isEnableVibrationBefore", "isEnableVibrationAfter",
        }
        assert isinstance(p["volume"], float)
        assert isinstance(p["temperature"], float)


def test_recipe_to_cloud_timestamp_is_epoch_millis():
    import time as _t

    before = int(_t.time() * 1000)
    ts = recipe_to_cloud(_sample_recipe())["createTimeStamp"]
    after = int(_t.time() * 1000) + 1000
    assert isinstance(ts, int)
    assert before - 1000 <= ts <= after


# ---------------------------------------------------------------------------
# RSA encrypt / chunking / base64 structure
# ---------------------------------------------------------------------------

def test_encrypt_form_is_base64_of_128_block_multiple():
    body = encrypt_form({"hello": "world"})
    raw = base64.b64decode(body)
    # Cipher length is always a multiple of the 128-byte RSA block size.
    assert len(raw) % RSA_KEY_BYTES == 0
    assert len(raw) == RSA_KEY_BYTES  # small payload → single block


def test_encrypt_form_chunks_large_payload():
    # A payload larger than one 117-byte plaintext block must span >1 cipher block.
    big = {"x": "y" * 500}
    raw = base64.b64decode(encrypt_form(big))
    plaintext_len = len(
        json.dumps(big, ensure_ascii=False, separators=(",", ":")).encode()
    )
    import math

    expected_blocks = math.ceil(plaintext_len / RSA_MAX_PLAIN_BLOCK)
    assert len(raw) == expected_blocks * RSA_KEY_BYTES
    assert expected_blocks >= 2


def test_rsa_roundtrip_with_local_key(monkeypatch):
    """Encrypt with a locally-generated key and decrypt to prove the chunking is
    lossless and reassembles the original plaintext."""
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    priv = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    pub = priv.public_key()
    der_b64 = base64.b64encode(
        pub.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode("ascii")

    # Swap the embedded key for our test key.
    monkeypatch.setattr(cloud, "RSA_PUBLIC_KEY_B64", der_b64)

    payload = {"msg": "z" * 400, "n": 42}
    body = encrypt_form(payload)
    raw = base64.b64decode(body)
    assert len(raw) % RSA_KEY_BYTES == 0

    # Decrypt block-by-block and reassemble.
    plaintext = bytearray()
    for i in range(0, len(raw), RSA_KEY_BYTES):
        block = raw[i:i + RSA_KEY_BYTES]
        plaintext.extend(priv.decrypt(block, padding.PKCS1v15()))
    assert json.loads(plaintext.decode("utf-8")) == payload


# ---------------------------------------------------------------------------
# Client request construction (HTTP monkeypatched — no network)
# ---------------------------------------------------------------------------

class _Recorder:
    """Captures _post calls and returns canned responses."""

    def __init__(self, response):
        self.calls = []
        self.response = response

    def __call__(self, endpoint, body, *, encrypted):
        self.calls.append({"endpoint": endpoint, "body": body, "encrypted": encrypted})
        return dict(self.response)


def _client(tmp_path, **kw):
    return XBloomCloud(auth_path=tmp_path / "auth.json", **kw)


def test_login_caches_token_and_member(tmp_path, monkeypatch):
    rec = _Recorder({"result": "success", "token": "TKN", "member": {"tableId": 777}})
    c = _client(tmp_path)
    monkeypatch.setattr(c, "_post", rec)
    c.login("a@b.com", "pw")
    assert c.token == "TKN"
    assert c.member_id == 777
    # login hit the login endpoint with an encrypted body.
    assert rec.calls[0]["endpoint"] == cloud.EP_LOGIN
    assert rec.calls[0]["encrypted"] is True
    # cache written & reloadable.
    c2 = _client(tmp_path)
    assert c2.token == "TKN" and c2.member_id == 777


def test_add_recipe_posts_encrypted_to_right_endpoint(tmp_path, monkeypatch):
    rec = _Recorder({"result": "success", "tableId": 999})
    c = _client(tmp_path, token="TKN", member_id=777)
    monkeypatch.setattr(c, "_post", rec)
    resp = c.add_recipe(_sample_recipe(), cup_type="xpod")
    assert resp["tableId"] == 999
    call = rec.calls[0]
    assert call["endpoint"] == cloud.EP_RECIPE_ADD
    assert call["encrypted"] is True
    # The body is the encrypted base64 string → decodes to a 128-multiple length.
    raw = base64.b64decode(call["body"])
    assert len(raw) % RSA_KEY_BYTES == 0


def test_add_recipe_requires_auth(tmp_path, monkeypatch):
    c = _client(tmp_path)  # no token / member
    monkeypatch.setattr(c, "_post", _Recorder({"result": "success"}))
    with pytest.raises(XBloomCloudError):
        c.add_recipe(_sample_recipe())


def test_add_recipe_encrypted_body_carries_auth_and_recipe(tmp_path, monkeypatch):
    """Decrypt the sent body with a local key to prove memberId+token+recipe are in it."""
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    priv = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    der_b64 = base64.b64encode(
        priv.public_key().public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo
        )
    ).decode("ascii")
    monkeypatch.setattr(cloud, "RSA_PUBLIC_KEY_B64", der_b64)

    rec = _Recorder({"result": "success", "tableId": 1})
    c = _client(tmp_path, token="TKN", member_id=777)
    monkeypatch.setattr(c, "_post", rec)
    c.add_recipe(_sample_recipe())

    raw = base64.b64decode(rec.calls[0]["body"])
    plain = bytearray()
    for i in range(0, len(raw), RSA_KEY_BYTES):
        plain.extend(priv.decrypt(raw[i:i + RSA_KEY_BYTES], padding.PKCS1v15()))
    form = json.loads(plain.decode("utf-8"))
    assert form["memberId"] == 777
    assert form["token"] == "TKN"
    assert form["skey"] == "testskey"
    assert form["theName"] == "Test Recipe"
    assert form["grandWater"] == 16.0
    # pourDataJSONStr survived encryption as a JSON string.
    assert isinstance(form["pourDataJSONStr"], str)
    assert len(json.loads(form["pourDataJSONStr"])) == 3


def test_delete_recipe_endpoint_and_id(tmp_path, monkeypatch):
    rec = _Recorder({"result": "success"})
    c = _client(tmp_path, token="TKN", member_id=1)
    monkeypatch.setattr(c, "_post", rec)
    c.delete_recipe(555)
    assert rec.calls[0]["endpoint"] == cloud.EP_RECIPE_DELETE


def test_update_recipe_endpoint(tmp_path, monkeypatch):
    rec = _Recorder({"result": "success"})
    c = _client(tmp_path, token="TKN", member_id=1)
    monkeypatch.setattr(c, "_post", rec)
    c.update_recipe(321, _sample_recipe())
    assert rec.calls[0]["endpoint"] == cloud.EP_RECIPE_UPDATE


def test_list_recipes_endpoint(tmp_path, monkeypatch):
    rec = _Recorder({"result": "success", "list": [{"tableId": 1, "theName": "x"}]})
    c = _client(tmp_path, token="TKN", member_id=1)
    monkeypatch.setattr(c, "_post", rec)
    resp = c.list_recipes()
    assert rec.calls[0]["endpoint"] == cloud.EP_RECIPE_LIST
    assert resp["list"][0]["tableId"] == 1


def test_fetch_public_is_plaintext_with_referer(tmp_path, monkeypatch):
    rec = _Recorder({"result": "success", "recipeVo": {"theName": "Shared"}})
    c = _client(tmp_path)
    monkeypatch.setattr(c, "_post", rec)
    c.fetch_public("https://share-h5.xbloom.com/?id=ABC123&x=1")
    call = rec.calls[0]
    assert call["endpoint"] == cloud.EP_RECIPE_PUBLIC
    assert call["encrypted"] is False
    # share id parsed out of the URL and sent as plain JSON dict.
    assert call["body"]["tableIdOfRSA"] == "ABC123"


def test_api_failure_raises(tmp_path, monkeypatch):
    c = _client(tmp_path, token="TKN", member_id=1)
    monkeypatch.setattr(c, "_post", _Recorder({"result": "fail", "info": "nope"}))
    with pytest.raises(XBloomCloudError):
        c.list_recipes()


def test_parse_share_id_variants():
    assert cloud.parse_share_id("ABC") == "ABC"
    assert cloud.parse_share_id("https://x/?id=XYZ&a=1") == "XYZ"
