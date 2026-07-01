"""xBloom **cloud** client — push recipes to your xBloom mobile-app account.

This is a **separate subsystem** from the BLE protocol in this package. Where the
BLE code talks to the machine over Bluetooth, this module talks to the xBloom
**cloud REST API** (``client-api.xbloom.com``), so a recipe you create here shows
up in the xBloom mobile app under your account.

⚠️ **Unofficial API.** There is no official, documented xBloom cloud API. Every
endpoint, field, and the encryption scheme below were **reverse-engineered by the
community** and may change or break without notice. Using it touches *your* xBloom
account; use your own account, at your own risk.

Credits / port
--------------
The REST mechanics (base URL, endpoints, the RSA-encrypted request-body scheme,
the static ``skey`` app key, and the recipe/pour field schema) were ported from
**``cryptofishbug/xbloom-recipe-cli``** (MIT) — specifically its ``xbloom_client.py``
and ``recipe_maker.py``. That project reverse-engineered the scheme from an HAR
capture plus APK decompilation. This module is a cleaned-up, typed re-port, with a
mapper from this package's own :class:`~xbloom_ble.recipe.Recipe` dataclass and two
extra endpoints (update / delete) modelled on the same pattern.

Wire format
-----------
* Base URL: ``https://client-api.xbloom.com/``.
* **Authenticated** request bodies are ``Base64( RSA-1024 PKCS#1 v1.5 ( JSON ) )``,
  encrypted Hutool-style in chunks: each 117-byte plaintext block encrypts to a
  128-byte cipher block, concatenated, then Base64-encoded and POSTed as the raw
  body (``Content-Type: application/json``). The 1024-bit RSA **public** key is
  embedded below (it is public; safe to ship).
* **Public** recipe fetch (``RecipeDetail.html``) is plain JSON with a
  ``Referer: https://share-h5.xbloom.com/`` header — no encryption, no auth.
* Auth: :meth:`XBloomCloud.login` POSTs ``skey="testskey"`` (a *static app key*, not
  a session token) plus email/password; the response yields a ``token`` and
  ``member.tableId``. Every authenticated call then carries ``memberId``
  (= ``member.tableId``) **and** ``token``.

The ``cryptography`` package is required. Install it via the ``cloud`` extra:
``pip install "xbloom-ble[cloud]"``. It is imported lazily so a BLE-only install
stays lean.
"""

from __future__ import annotations

import base64
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote
from urllib.request import Request, urlopen

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .recipe import Pour, Recipe

__all__ = [
    "XBloomCloud",
    "XBloomCloudError",
    "recipe_to_cloud",
    "rsa_encrypt",
    "encrypt_form",
    "BASE_URL",
    "CUP_TYPES",
    "PATTERN_CODES",
    "MANAGED_PREFIX",
]

# Recipes this tool creates are named ``AUTO <name>``. The sync/guard logic will
# ONLY ever update or delete recipes whose name starts with this prefix — the
# user's own recipes (any other name) are never modified or removed.
MANAGED_PREFIX = "AUTO "


def _is_managed(name: object) -> bool:
    """True if ``name`` is a tool-owned recipe (starts with ``MANAGED_PREFIX``)."""
    return isinstance(name, str) and name.startswith(MANAGED_PREFIX)

# ---------------------------------------------------------------------------
# Constants (ported verbatim from cryptofishbug/xbloom-recipe-cli, MIT)
# ---------------------------------------------------------------------------

BASE_URL = "https://client-api.xbloom.com/"

# Static app key, hardcoded in the app (ReleaseKey.java). NOT a session token —
# the real per-user auth is (memberId + token) from login.
SKEY = "testskey"

# RSA 1024-bit **public** key, X.509 SubjectPublicKeyInfo DER, base64-encoded.
# Copied verbatim from the upstream source (public key — safe to embed).
RSA_PUBLIC_KEY_B64 = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC4LF40GZ72SdhMyl765K/i4nY5"
    "CPcHz2Q1IKWKZ9S79xmK7G8pUhbVf4EZLvnNF1+9IvOFQUKV5Z7ZNNviqSpnql9"
    "tAT+8+J/He0R7pcirvVSxgdr2i9V/C/gmqAEZ5qVTzRnd3uWdFoKzPdEBxP0Ipor"
    "J1VBbCv90yBSOhVxO+QIDAQAB"
)

# RSA-1024: key size 128 bytes; PKCS#1 v1.5 leaves 128 - 11 = 117 plaintext bytes.
RSA_KEY_BYTES = 128
RSA_MAX_PLAIN_BLOCK = 117

INTERFACE_VERSION = 20240918
PUBLIC_INTERFACE_VERSION = 19700101

# Endpoints
EP_LOGIN = "tMemberLogin.thtml"
EP_RECIPE_ADD = "tuRecipeAdd.tuhtml"
EP_RECIPE_UPDATE = "tuRecipeUpdate.tuhtml"
EP_RECIPE_DELETE = "tuRecipeDelete.tuhtml"
EP_RECIPE_LIST = "tuMyTeaRecipeCreated.tuhtml"
EP_RECIPE_PUBLIC = "RecipeDetail.html"

# Cup types accepted by the cloud (per the task spec / app enum).
CUP_TYPES = {"xpod": 1, "xdripper": 2, "other": 3, "tea": 4}

# Pour-pattern codes on the cloud side. Verified against real app-made recipes:
# spiral/ring/circular pours all encode as 2, center as 1. (An earlier port used
# spiral=3, which did not match what the app actually stores.)
PATTERN_CODES = {"center": 1, "circular": 2, "ring": 2, "spiral": 2}

# The cloud encodes booleans as 1 = ON/true, 2 = OFF/false (never true/false).
CLOUD_TRUE = 1
CLOUD_FALSE = 2


def _cloud_bool(value: bool) -> int:
    """Encode a Python bool the way the cloud expects: 1 = on/true, 2 = off/false."""
    return CLOUD_TRUE if value else CLOUD_FALSE


# Default auth-cache location. Overridable per-client and via XBLOOM_CLOUD_AUTH.
DEFAULT_AUTH_PATH = Path.home() / ".config" / "xbloom-ble" / "cloud-auth.json"


class XBloomCloudError(RuntimeError):
    """Raised on any cloud client error (missing dep, auth, or API failure)."""


# ---------------------------------------------------------------------------
# Encryption (ported; requires `cryptography`, imported lazily)
# ---------------------------------------------------------------------------

def _require_cryptography():
    """Import and return the cryptography primitives, with a helpful error."""
    try:
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.serialization import (
            load_der_public_key,
        )
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise XBloomCloudError(
            "The cloud client needs the 'cryptography' package. Install it with:\n"
            '    pip install "xbloom-ble[cloud]"'
        ) from exc
    return load_der_public_key, padding


def _load_rsa_public_key():
    load_der_public_key, _ = _require_cryptography()
    der = base64.b64decode(RSA_PUBLIC_KEY_B64)
    return load_der_public_key(der)


def rsa_encrypt(plaintext: bytes) -> bytes:
    """RSA-1024 PKCS#1 v1.5 encrypt with Hutool-style chunking.

    Splits ``plaintext`` into 117-byte blocks and encrypts each to a 128-byte
    cipher block, concatenating the results. The output length is therefore
    always a multiple of 128.
    """
    _, padding = _require_cryptography()
    pub_key = _load_rsa_public_key()
    num_blocks = max(1, math.ceil(len(plaintext) / RSA_MAX_PLAIN_BLOCK))
    ciphertext = bytearray()
    for i in range(num_blocks):
        start = i * RSA_MAX_PLAIN_BLOCK
        end = min(start + RSA_MAX_PLAIN_BLOCK, len(plaintext))
        block = plaintext[start:end]
        ciphertext.extend(pub_key.encrypt(block, padding.PKCS1v15()))
    return bytes(ciphertext)


def encrypt_form(form: dict[str, Any]) -> str:
    """JSON-serialize ``form`` → RSA-encrypt → Base64. Returns the POST body string."""
    json_bytes = json.dumps(
        form, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return base64.b64encode(rsa_encrypt(json_bytes)).decode("ascii")


# ---------------------------------------------------------------------------
# Recipe → cloud-schema mapper
# ---------------------------------------------------------------------------

def _pour_to_cloud(pour: "Pour", index: int) -> dict[str, Any]:
    """Map one :class:`~xbloom_ble.recipe.Pour` to the cloud pour schema.

    * ``pattern`` → 1/2 (center/circular; this package's spiral & ring → 2).
    * ``agitation`` → ``isEnableVibrationAfter`` (agitate *after* the pour, e.g.
      the recipe's "agitation after bloom"). 1 = on, 2 = off.
    * booleans are encoded 1 = on, 2 = off (never true/false).
    """
    pattern_code = PATTERN_CODES.get(pour.pattern)
    if pattern_code is None:
        raise XBloomCloudError(
            f"pour #{index + 1}: unknown pattern {pour.pattern!r} "
            f"(known: {sorted(PATTERN_CODES)})"
        )
    return {
        "theName": f"Pour{index + 1}" if index else "Bloom",
        "volume": float(pour.ml),
        "temperature": float(pour.temp_c),
        "flowRate": float(pour.flow_ml_s),
        "pattern": pattern_code,
        "pausing": int(pour.pause_s),
        "isEnableVibrationBefore": _cloud_bool(False),
        "isEnableVibrationAfter": _cloud_bool(bool(pour.agitation)),
    }


def recipe_to_cloud(
    recipe: "Recipe",
    *,
    cup_type: str | int = "xdripper",
    adapted_model: int = 1,
    the_color: str = "#C9D5B8",
    bypass_temp: float = 85.0,
    bypass_volume: float = 0.0,
) -> dict[str, Any]:
    """Map this package's :class:`~xbloom_ble.recipe.Recipe` to the cloud recipe dict.

    Returns the recipe-specific fields only (no auth / base-form fields — those are
    added by :class:`XBloomCloud` at request time). Notable mappings:

    * ``grandWater`` = the brew **ratio** (``recipe.effective_ratio``), *not* total
      water — this is how the xBloom cloud encodes it.
    * ``grinderSize`` = ``recipe.grind``; ``rpm`` = the first pour's rpm.
    * ``cupType`` accepts a name (``xpod``/``xdripper``/``other``/``tea``) or the
      raw int code.
    * ``pourDataJSONStr`` is the pour list JSON-*stringified* (a string, not an
      array) — the cloud expects a string here.
    * booleans throughout are 1 = on, 2 = off.

    ``createTimeStamp`` is milliseconds since the epoch, stamped at call time.
    """
    if isinstance(cup_type, str):
        code = CUP_TYPES.get(cup_type.lower())
        if code is None:
            raise XBloomCloudError(
                f"unknown cup_type {cup_type!r} (known: {sorted(CUP_TYPES)})"
            )
        cup_type_code = code
    else:
        cup_type_code = int(cup_type)

    pour_list = [_pour_to_cloud(p, i) for i, p in enumerate(recipe.pours)]
    first_rpm = int(recipe.pours[0].rpm) if recipe.pours else 0

    bypass_on = float(bypass_volume) > 0

    return {
        "theName": recipe.name,
        "dose": float(recipe.dose_g),
        # grandWater is the RATIO, not the total water volume.
        "grandWater": float(recipe.effective_ratio),
        "grinderSize": float(recipe.grind),
        "rpm": first_rpm,
        "cupType": cup_type_code,
        "adaptedModel": int(adapted_model),
        "isEnableBypassWater": _cloud_bool(bypass_on),
        "isSetGrinderSize": _cloud_bool(True),
        "theColor": the_color,
        "theSubsetId": 0,
        "bypassTemp": float(bypass_temp),
        "bypassVolume": float(bypass_volume),
        "subSetType": 2,  # 2 = user-made recipe
        "appPlace": [4],  # 4 = show under "My recipes"
        "createTimeStamp": int(time.time() * 1000),
        "isShortcuts": CLOUD_FALSE,  # 2 = normal recipe (not a shortcut)
        "pourDataJSONStr": json.dumps(
            pour_list, ensure_ascii=False, separators=(",", ":")
        ),
    }


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------

def parse_share_id(share_url_or_id: str) -> str:
    """Extract the recipe share id from a full share URL or a bare id string."""
    s = share_url_or_id.strip()
    if "id=" in s:
        s = s.split("id=")[-1].split("&")[0]
    return unquote(s)


@dataclass
class XBloomCloud:
    """Client for the (unofficial) xBloom cloud recipe API.

    Credentials come from the constructor args or the ``XBLOOM_EMAIL`` /
    ``XBLOOM_PASSWORD`` environment variables — never hardcode them. After
    :meth:`login`, the ``token`` and ``member_id`` are cached to ``auth_path``
    (default ``~/.config/xbloom-ble/cloud-auth.json``, overridable via the
    constructor or the ``XBLOOM_CLOUD_AUTH`` env var) so later calls reuse them.

    All HTTP goes through :meth:`_post`; tests monkeypatch that to avoid the
    network.
    """

    email: str | None = None
    password: str | None = None
    auth_path: Path | None = None
    timeout: float = 15.0
    token: str = ""
    member_id: int = 0

    def __post_init__(self) -> None:
        self.email = self.email or os.environ.get("XBLOOM_EMAIL")
        self.password = self.password or os.environ.get("XBLOOM_PASSWORD")
        if self.auth_path is None:
            env = os.environ.get("XBLOOM_CLOUD_AUTH")
            self.auth_path = Path(env) if env else DEFAULT_AUTH_PATH
        # Reuse a cached token if present so callers can skip an explicit login.
        if not self.token or not self.member_id:
            self._load_cached_auth()

    # -- auth cache ---------------------------------------------------------
    def _load_cached_auth(self) -> None:
        try:
            data = json.loads(Path(self.auth_path).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        self.token = self.token or str(data.get("token", ""))
        self.member_id = self.member_id or int(data.get("member_id", 0) or 0)

    def _save_cached_auth(self) -> None:
        path = Path(self.auth_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"member_id": self.member_id, "token": self.token},
            ),
            encoding="utf-8",
        )
        try:
            path.chmod(0o600)
        except OSError:  # pragma: no cover - non-POSIX filesystems
            pass

    # -- base form ----------------------------------------------------------
    def _base_form(self) -> dict[str, Any]:
        """Common auth/envelope fields on every authenticated request."""
        return {
            "interfaceVersion": INTERFACE_VERSION,
            "skey": SKEY,
            "phoneType": "Android",
            "memberId": self.member_id,
            "clientType": 2,
            "languageType": 1,
            "token": self.token,
        }

    def _require_auth(self) -> None:
        if not self.member_id or not self.token:
            raise XBloomCloudError(
                "not authenticated — call login() first (or set XBLOOM_EMAIL / "
                "XBLOOM_PASSWORD and a valid cached token)"
            )

    # -- HTTP ---------------------------------------------------------------
    def _post(
        self, endpoint: str, body: str | dict[str, Any], *, encrypted: bool
    ) -> dict[str, Any]:
        """POST to the cloud. Encrypted bodies are a raw base64 string; public
        bodies are plain JSON with a share-h5 Referer.

        Tests monkeypatch this method to avoid real network calls.
        """
        url = BASE_URL + endpoint
        if encrypted:
            assert isinstance(body, str)
            data = body.encode("utf-8")
        else:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        req = Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        req.add_header("Accept", "application/json, text/plain, */*")
        if not encrypted:
            req.add_header("Referer", "https://share-h5.xbloom.com/")

        with urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - fixed host
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _check(resp: dict[str, Any], what: str) -> dict[str, Any]:
        if resp.get("result") != "success":
            raise XBloomCloudError(f"{what} failed: {resp.get('info', resp)}")
        return resp

    # -- public API ---------------------------------------------------------
    def login(self, email: str | None = None, password: str | None = None) -> dict[str, Any]:
        """Authenticate and cache ``token`` + ``member_id``.

        Credentials resolve from the args, then the constructor, then the
        ``XBLOOM_EMAIL`` / ``XBLOOM_PASSWORD`` env vars.
        """
        email = email or self.email
        password = password or self.password
        if not email or not password:
            raise XBloomCloudError(
                "login needs an email and password (args, constructor, or "
                "XBLOOM_EMAIL / XBLOOM_PASSWORD env vars)"
            )
        form = {
            "interfaceVersion": INTERFACE_VERSION,
            "skey": SKEY,
            "phoneType": "Android",
            "clientType": 2,
            "languageType": 1,
            "email": email,
            "password": password,
            "jpushId": "",
        }
        resp = self._post(EP_LOGIN, encrypt_form(form), encrypted=True)
        self._check(resp, "login")
        member = resp.get("member", {})
        self.member_id = int(member.get("tableId", 0) or 0)
        self.token = str(resp.get("token", ""))
        self._save_cached_auth()
        return resp

    def add_recipe(self, recipe: "Recipe | dict[str, Any]", **map_kwargs: Any) -> dict[str, Any]:
        """Create a recipe in the account. ``recipe`` is either a
        :class:`~xbloom_ble.recipe.Recipe` (mapped via :func:`recipe_to_cloud`,
        passing ``map_kwargs`` through) or an already-mapped cloud dict.

        Returns the API response (includes the new recipe's ``tableId``).
        """
        self._require_auth()
        cloud_recipe = self._as_cloud_dict(recipe, map_kwargs)
        form = self._base_form()
        form.update(cloud_recipe)
        resp = self._post(EP_RECIPE_ADD, encrypt_form(form), encrypted=True)
        return self._check(resp, "add_recipe")

    def update_recipe(
        self,
        table_id: int | str,
        recipe: "Recipe | dict[str, Any]",
        *,
        require_managed: bool = True,
        **map_kwargs: Any,
    ) -> dict[str, Any]:
        """Update an existing recipe by its ``tableId``.

        By default this **refuses** to touch a recipe whose name is not
        ``AUTO …`` (``require_managed``) — so it can never clobber a recipe the
        user created by hand. :meth:`sync_recipe` passes ``require_managed=False``
        because it has already resolved a managed target.
        """
        self._require_auth()
        if require_managed:
            self._assert_managed(table_id)
        cloud_recipe = self._as_cloud_dict(recipe, map_kwargs)
        form = self._base_form()
        form.update(cloud_recipe)
        form["tableId"] = table_id
        resp = self._post(EP_RECIPE_UPDATE, encrypt_form(form), encrypted=True)
        return self._check(resp, "update_recipe")

    def delete_recipe(
        self, table_id: int | str, *, require_managed: bool = True
    ) -> dict[str, Any]:
        """Delete a recipe by its ``tableId``.

        Refuses to delete a non-``AUTO …`` recipe unless ``require_managed`` is
        cleared — the user's own recipes are protected.
        """
        self._require_auth()
        if require_managed:
            self._assert_managed(table_id)
        form = self._base_form()
        form["tableId"] = table_id
        resp = self._post(EP_RECIPE_DELETE, encrypt_form(form), encrypted=True)
        return self._check(resp, "delete_recipe")

    # -- managed ('AUTO …') sync -------------------------------------------
    def recipe_items(self) -> list[dict[str, Any]]:
        """All recipes in the account as raw cloud dicts (``theName``/``tableId``…)."""
        self._require_auth()
        return list(self.list_recipes(adapted_model=0).get("list", []) or [])

    def managed_recipes(self) -> list[dict[str, Any]]:
        """Only the recipes this tool owns — name starts with ``MANAGED_PREFIX``."""
        return [r for r in self.recipe_items() if _is_managed(r.get("theName", ""))]

    def _assert_managed(self, table_id: int | str) -> None:
        """Raise unless ``table_id`` names an ``AUTO …`` (tool-owned) recipe."""
        for r in self.recipe_items():
            if str(r.get("tableId")) == str(table_id):
                name = str(r.get("theName", ""))
                if not _is_managed(name):
                    raise XBloomCloudError(
                        f"refusing to modify {name!r}: only recipes named "
                        f"'{MANAGED_PREFIX}…' are managed by this tool — your own "
                        f"recipes are never changed."
                    )
                return
        raise XBloomCloudError(f"recipe tableId={table_id} not found in this account")

    def sync_recipe(
        self,
        recipe: "Recipe | dict[str, Any]",
        *,
        prefix: str = MANAGED_PREFIX,
        **map_kwargs: Any,
    ) -> tuple[dict[str, Any], str]:
        """Create-or-update a recipe by name (idempotent).

        The recipe's ``theName`` is prefixed with ``prefix`` (default
        ``MANAGED_PREFIX`` = ``"AUTO "``) so tool-owned recipes are unmistakable.
        Pass ``prefix=""`` to sync under the recipe's own name (e.g. when the
        caller manages recipes by a deterministic name of its own). If a recipe
        with that exact name already exists it is **updated in place**; otherwise
        a new one is **added**. Returns ``(response, "added" | "updated")``.
        """
        self._require_auth()
        cloud = dict(self._as_cloud_dict(recipe, map_kwargs))
        name = str(cloud.get("theName", "")).strip()
        if prefix and not name.startswith(prefix):
            name = prefix + name
        cloud["theName"] = name
        existing = next(
            (r for r in self.recipe_items() if str(r.get("theName", "")) == name), None
        )
        if existing is not None:
            resp = self.update_recipe(existing["tableId"], cloud, require_managed=False)
            return resp, "updated"
        return self.add_recipe(cloud), "added"

    def prune_managed(self, keep_names: "list[str] | set[str]") -> list[str]:
        """Delete tool-owned recipes whose name is not in ``keep_names``.

        ``keep_names`` may be given with or without the ``AUTO `` prefix. Only
        ``AUTO …`` recipes are ever considered. Returns the names deleted.
        """
        keep = {n if _is_managed(n) else MANAGED_PREFIX + n for n in keep_names}
        deleted: list[str] = []
        for r in self.managed_recipes():
            name = str(r.get("theName", ""))
            if name not in keep:
                self.delete_recipe(r["tableId"], require_managed=False)
                deleted.append(name)
        return deleted

    def list_recipes(self, adapted_model: int = 1) -> dict[str, Any]:
        """List the account's created recipes (``adapted_model``: 0=all,
        1=Original, 2=Studio)."""
        self._require_auth()
        form = self._base_form()
        form["pageNumber"] = 1
        form["countPerPage"] = 100
        if adapted_model:
            form["adaptedModel"] = adapted_model
        resp = self._post(EP_RECIPE_LIST, encrypt_form(form), encrypted=True)
        return self._check(resp, "list_recipes")

    def fetch_public(self, share_id_or_url: str) -> dict[str, Any]:
        """Fetch a publicly shared recipe (no auth, plain JSON)."""
        body = {
            "tableIdOfRSA": parse_share_id(share_id_or_url),
            "interfaceVersion": PUBLIC_INTERFACE_VERSION,
            "skey": SKEY,
        }
        resp = self._post(EP_RECIPE_PUBLIC, body, encrypted=False)
        return self._check(resp, "fetch_public")

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _as_cloud_dict(
        recipe: "Recipe | dict[str, Any]", map_kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        if isinstance(recipe, dict):
            return recipe
        return recipe_to_cloud(recipe, **map_kwargs)
