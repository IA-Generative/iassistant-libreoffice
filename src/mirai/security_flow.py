import base64
import ctypes
import email.utils
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse

from ctypes import wintypes

from . import local_config


def _b64e(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(value):
    text = str(value or "").strip()
    if not text:
        return b""
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


_CONTENT_TYPE_JSON = "application/json"


def _json_bytes(payload):
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


class SecureFlowError(Exception):
    pass


class HttpStatusError(SecureFlowError):
    def __init__(self, status, body="", headers=None):
        super(HttpStatusError, self).__init__(f"HTTP {status}")
        self.status = int(status)
        self.body = body or ""
        self.headers = headers or {}


class VaultError(SecureFlowError):
    pass


class _BaseFileStore(object):
    """Common atomic read/write logic for JSON-backed file stores."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()

    def _ensure_parent(self):
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _read_raw(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    def write(self, payload):
        if local_config.is_frozen():
            return
        with self._lock:
            self._ensure_parent()
            dir_path = os.path.dirname(self.path) or None
            fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=dir_path)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                os.replace(tmp_path, self.path)
            finally:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except OSError:
                    pass


class FileJsonStore(_BaseFileStore):
    def read(self):
        with self._lock:
            data = self._read_raw()
            return data if isinstance(data, dict) else {}


class FileQueueStore(_BaseFileStore):
    def read(self):
        with self._lock:
            data = self._read_raw()
            return data if isinstance(data, list) else []


class MemoryVault(object):
    def __init__(self):
        self._data = {}
        self._lock = threading.RLock()

    def get_secret(self, service, account):
        key = f"{service}:{account}"
        with self._lock:
            return self._data.get(key)

    def set_secret(self, service, account, value):
        key = f"{service}:{account}"
        with self._lock:
            self._data[key] = str(value)


class MacKeychainVault(object):
    def get_secret(self, service, account):
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()

    def set_secret(self, service, account, value):
        proc = subprocess.run(
            ["security", "add-generic-password", "-U", "-s", service, "-a", account, "-w", str(value)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise VaultError("Keychain write failed")


class LinuxSecretToolVault(object):
    def get_secret(self, service, account):
        proc = subprocess.run(
            ["secret-tool", "lookup", "service", service, "account", account],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()

    def set_secret(self, service, account, value):
        proc = subprocess.run(
            ["secret-tool", "store", "--label", "MIrAI device key", "service", service, "account", account],
            input=str(value),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise VaultError("SecretService write failed")


class _WinCredentialAttribute(ctypes.Structure):
    _fields_ = [
        ("Keyword", wintypes.LPWSTR),
        ("Flags", wintypes.DWORD),
        ("ValueSize", wintypes.DWORD),
        ("Value", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class _WinCredential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.POINTER(_WinCredentialAttribute)),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredmanVault(object):
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2

    def __init__(self):
        self._advapi = ctypes.WinDLL("Advapi32.dll")
        self._advapi.CredReadW.argtypes = [
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_WinCredential)),
        ]
        self._advapi.CredReadW.restype = wintypes.BOOL
        self._advapi.CredWriteW.argtypes = [ctypes.POINTER(_WinCredential), wintypes.DWORD]
        self._advapi.CredWriteW.restype = wintypes.BOOL
        self._advapi.CredFree.argtypes = [ctypes.c_void_p]
        self._advapi.CredFree.restype = None

    def _target(self, service, account):
        return f"{service}:{account}"

    def get_secret(self, service, account):
        cred_ptr = ctypes.POINTER(_WinCredential)()
        ok = self._advapi.CredReadW(self._target(service, account), self.CRED_TYPE_GENERIC, 0, ctypes.byref(cred_ptr))
        if not ok:
            return None
        try:
            cred = cred_ptr.contents
            raw = ctypes.string_at(cred.CredentialBlob, int(cred.CredentialBlobSize))
            return raw.decode("utf-8")
        finally:
            self._advapi.CredFree(cred_ptr)

    def set_secret(self, service, account, value):
        raw = str(value).encode("utf-8")
        blob = (ctypes.c_ubyte * len(raw))(*raw)
        cred = _WinCredential()
        cred.Type = self.CRED_TYPE_GENERIC
        cred.TargetName = self._target(service, account)
        cred.CredentialBlobSize = len(raw)
        cred.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        cred.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        cred.UserName = account
        ok = self._advapi.CredWriteW(ctypes.byref(cred), 0)
        if not ok:
            raise VaultError("CredMan write failed")


def default_vault():
    if sys.platform == "darwin":
        return MacKeychainVault()
    if sys.platform.startswith("linux"):
        return LinuxSecretToolVault()
    if sys.platform.startswith("win"):
        return WindowsCredmanVault()
    return MemoryVault()


class Ed25519Provider(object):
    def __init__(self):
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except Exception as exc:
            raise SecureFlowError(
                "ed25519 backend unavailable (install 'cryptography' in LibreOffice Python)"
            ) from exc
        self._serialization = serialization
        self._priv_cls = Ed25519PrivateKey

    def generate_keypair(self):
        private = self._priv_cls.generate()
        private_raw = private.private_bytes(
            encoding=self._serialization.Encoding.Raw,
            format=self._serialization.PrivateFormat.Raw,
            encryption_algorithm=self._serialization.NoEncryption(),
        )
        public_raw = private.public_key().public_bytes(
            encoding=self._serialization.Encoding.Raw,
            format=self._serialization.PublicFormat.Raw,
        )
        return private_raw, public_raw

    def public_from_private(self, private_raw):
        private = self._priv_cls.from_private_bytes(private_raw)
        return private.public_key().public_bytes(
            encoding=self._serialization.Encoding.Raw,
            format=self._serialization.PublicFormat.Raw,
        )

    def sign(self, private_raw, payload_bytes):
        private = self._priv_cls.from_private_bytes(private_raw)
        return private.sign(payload_bytes)


class SecureBootstrapFlow(object):
    def __init__(
        self,
        bootstrap_base_url,
        plugin_uuid,
        device_name,
        http_call,
        log_func=None,
        state_store=None,
        queue_store=None,
        vault=None,
        signer=None,
        clock=None,
        queue_ttl_seconds=86400,
        min_refresh_seconds=30,
    ):
        self.bootstrap_base_url = (bootstrap_base_url or "").rstrip("/")
        self.plugin_uuid = str(plugin_uuid or "").strip()
        self.device_name = str(device_name or "mirai-libreoffice").strip() or "mirai-libreoffice"
        self._http_call = http_call
        self._log = log_func or (lambda *_args, **_kwargs: None)
        self._clock = clock or time.time
        self._state_store = state_store
        self._queue_store = queue_store
        self._vault = vault or default_vault()
        self._signer = signer or Ed25519Provider()
        self._lock = threading.RLock()
        self._queue_ttl_seconds = int(queue_ttl_seconds)
        self._min_refresh_seconds = int(min_refresh_seconds)
        self._clock_skew_seconds = 0

        self._state = self._state_store.read() if self._state_store else {}
        if not isinstance(self._state, dict):
            self._state = {}
        if "needs_rebind" not in self._state:
            self._state["needs_rebind"] = False

    @staticmethod
    def _safe_log_error(exc):
        text = str(exc or "")
        lowered = text.lower()
        for marker in ("token", "secret", "authorization", "bearer", "password"):
            if marker in lowered:
                return "sensitive error hidden"
        return text

    def _effective_now(self):
        skew = self._clock_skew_seconds if self._clock_skew_seconds > 0 else 0
        return float(self._clock()) + float(skew)

    def _save_state(self):
        if self._state_store:
            self._state_store.write(self._state)

    def _queue_load(self):
        if not self._queue_store:
            return []
        data = self._queue_store.read()
        return data if isinstance(data, list) else []

    def _queue_save(self, items):
        if self._queue_store:
            self._queue_store.write(items)

    def _bootstrap_url(self, suffix):
        base = self.bootstrap_base_url
        suffix = "/" + str(suffix or "").lstrip("/")
        return base + suffix

    def _default_telemetry_endpoint(self):
        parsed = urllib.parse.urlparse(self.bootstrap_base_url)
        if not parsed.scheme or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}/telemetry/v1/traces"

    def _update_clock_skew(self, headers):
        date_value = ""
        if isinstance(headers, dict):
            date_value = headers.get("Date") or headers.get("date") or ""
        if not date_value:
            return
        try:
            dt = email.utils.parsedate_to_datetime(date_value)
            server_ts = dt.timestamp()
            skew = float(server_ts - time.time())
            with self._lock:
                self._clock_skew_seconds = skew
            if abs(skew) > 120:
                self._log(f"[SECURE] clock skew detected: {int(skew)}s")
        except ValueError:
            pass

    def _request_json(self, method, url, body=None, headers=None, timeout=10, use_proxy=False):
        req_headers = {"Accept": _CONTENT_TYPE_JSON}
        if isinstance(headers, dict):
            req_headers.update(headers)
        payload = _json_bytes(body) if body is not None else None
        if payload is not None and "Content-Type" not in req_headers:
            req_headers["Content-Type"] = _CONTENT_TYPE_JSON
        if use_proxy is None:
            attempts = [False, True]
        else:
            attempts = [bool(use_proxy)]

        last_exc = None
        for proxy_mode in attempts:
            try:
                status, resp_headers, raw_body = self._http_call(
                    method=method,
                    url=url,
                    headers=req_headers,
                    body=payload,
                    timeout=timeout,
                    use_proxy=proxy_mode,
                )
                self._update_clock_skew(resp_headers if isinstance(resp_headers, dict) else {})
                text = raw_body.decode("utf-8") if isinstance(raw_body, (bytes, bytearray)) else str(raw_body or "")
                if int(status) < 200 or int(status) >= 300:
                    raise HttpStatusError(status=status, body=text, headers=resp_headers)
                if not text.strip():
                    return {}
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
                raise SecureFlowError("JSON response is not an object")
            # Application-level errors (SecureFlowError) are authoritative and propagate:
            # only network errors justify retrying through another path.
            except urllib.error.URLError as exc:
                last_exc = exc
        if last_exc:
            raise last_exc
        raise SecureFlowError("request failed")

    def _private_key_account(self):
        return f"plugin:{self.plugin_uuid}:ed25519"

    def ensure_identity(self):
        with self._lock:
            if not self.plugin_uuid:
                raise SecureFlowError("plugin_uuid is required")
            encoded_private = None
            try:
                encoded_private = self._vault.get_secret("mirai-libreoffice", self._private_key_account())
            except Exception as exc:
                raise VaultError(f"secure storage read failed: {self._safe_log_error(exc)}")
            if encoded_private:
                private_raw = _b64d(encoded_private)
                public_raw = self._signer.public_from_private(private_raw)
            else:
                private_raw, public_raw = self._signer.generate_keypair()
                try:
                    self._vault.set_secret("mirai-libreoffice", self._private_key_account(), _b64e(private_raw))
                except Exception as exc:
                    raise VaultError(f"secure storage write failed: {self._safe_log_error(exc)}")
            self._state["plugin_uuid"] = self.plugin_uuid
            self._state["public_key"] = _b64e(public_raw)
            self._save_state()
            return self._state["public_key"]

    def _private_key(self):
        encoded_private = self._vault.get_secret("mirai-libreoffice", self._private_key_account())
        if not encoded_private:
            raise SecureFlowError("private key missing from secure storage")
        return _b64d(encoded_private)

    def _sign_payload(self, payload_bytes):
        private_raw = self._private_key()
        signature = self._signer.sign(private_raw, payload_bytes)
        return _b64e(signature)

    def _token_valid(self, token_data, min_validity=None):
        if not isinstance(token_data, dict):
            return False
        token = str(token_data.get("token") or "").strip()
        expires_at = float(token_data.get("expires_at") or 0)
        min_secs = self._min_refresh_seconds if min_validity is None else int(min_validity)
        return bool(token) and (expires_at - self._effective_now()) >= min_secs

    def _store_token(self, token, expires_in=None, expires_at=None, kind="preauth"):
        token_text = str(token or "").strip()
        if not token_text:
            raise SecureFlowError("telemetry token is empty")
        if expires_at is None:
            ttl = int(expires_in or 0)
            expires_at = self._effective_now() + max(ttl, 60)
        self._state["telemetry"] = {
            "token": token_text,
            "expires_at": float(expires_at),
            "kind": str(kind or "preauth"),
        }
        self._state["needs_rebind"] = False
        self._save_state()

    @staticmethod
    def _extract_token(response):
        if not isinstance(response, dict):
            return "", None, None
        token = (
            response.get("telemetry_token")
            or response.get("telemetryKey")
            or response.get("telemetry_key")
            or response.get("token")
            or response.get("access_token")
            or response.get("preauth_token")
            or ""
        )
        expires_in = (
            response.get("expires_in")
            or response.get("telemetryKeyTtlSeconds")
            or response.get("ttl")
        )
        expires_at = (
            response.get("expires_at")
            or response.get("telemetryKeyExpiresAt")
            or response.get("exp")
        )
        return token, expires_in, expires_at

    def fetch_bootstrap_config(self):
        with self._lock:
            if not self.bootstrap_base_url:
                raise SecureFlowError("bootstrap base URL is empty")
            url = self._bootstrap_url("/config/libreoffice/config.json")
            data = self._request_json("GET", url, use_proxy=None, timeout=10)
            self._state["bootstrap_config"] = data
            self._save_state()
            return data

    def _enroll_url(self):
        return self._bootstrap_url("/enroll")

    def _enroll_confirm_url(self):
        return self._bootstrap_url("/enroll/confirm")

    def _telemetry_token_url(self):
        return self._bootstrap_url("/telemetry/token")

    def _bind_url(self):
        return self._bootstrap_url("/identity/bind")

    def _challenge_bytes(self, challenge):
        text = str(challenge or "")
        if not text:
            raise SecureFlowError("enroll challenge is empty")
        try:
            decoded = _b64d(text)
            if decoded:
                return decoded
        except ValueError:
            pass
        return text.encode("utf-8")

    def enroll_anonymous(self):
        with self._lock:
            public_key = self.ensure_identity()
            payload = {
                "plugin_uuid": self.plugin_uuid,
                "device_name": self.device_name,
                "public_key": public_key,
            }
            enroll_resp = self._request_json("POST", self._enroll_url(), body=payload, timeout=10, use_proxy=None)
            token, expires_in, expires_at = self._extract_token(enroll_resp)
            enroll_id = str(enroll_resp.get("enroll_id") or enroll_resp.get("id") or "").strip()
            challenge = enroll_resp.get("challenge")
            if enroll_id and challenge:
                signature = self._sign_payload(self._challenge_bytes(challenge))
                confirm_payload = {
                    "enroll_id": enroll_id,
                    "plugin_uuid": self.plugin_uuid,
                    "signature": signature,
                    "public_key": public_key,
                }
                try:
                    confirm_resp = self._request_json(
                        "POST",
                        self._enroll_confirm_url(),
                        body=confirm_payload,
                        timeout=10,
                        use_proxy=None,
                    )
                    token, expires_in, expires_at = self._extract_token(confirm_resp)
                except HttpStatusError as exc:
                    if exc.status != 404:
                        raise

            if not token:
                # Compatibility path for bootstrap services exposing /enroll but no /enroll/confirm.
                try:
                    response = self._request_json(
                        "GET",
                        self._telemetry_token_url(),
                        timeout=10,
                        use_proxy=None,
                    )
                    token, expires_in, expires_at = self._extract_token(response)
                except HttpStatusError as exc:
                    if exc.status not in (401, 403, 404):
                        raise

            if enroll_id:
                self._state["enroll_id"] = enroll_id
            elif not self._state.get("enroll_id"):
                self._state["enroll_id"] = self.plugin_uuid
            self._state["enrolled_at"] = self._effective_now()
            if token:
                self._store_token(token, expires_in=expires_in, expires_at=expires_at, kind="preauth")
            self._save_state()
            return self._state.get("telemetry", {}).get("token", "")

    def _device_proof(self):
        nonce = _b64e(os.urandom(16))
        payload = {
            "plugin_uuid": self.plugin_uuid,
            "enroll_id": self._state.get("enroll_id", ""),
            "public_key": self._state.get("public_key", ""),
            "nonce": nonce,
            "timestamp": int(self._effective_now()),
        }
        payload["signature"] = self._sign_payload(_json_bytes(payload))
        return payload

    def refresh_telemetry_token(self, force=False):
        with self._lock:
            token_data = self._state.get("telemetry", {})
            if not force and self._token_valid(token_data):
                return token_data.get("token", "")
            enroll_id = str(self._state.get("enroll_id") or "").strip()
            if not enroll_id:
                try:
                    response = self._request_json(
                        "GET",
                        self._telemetry_token_url(),
                        timeout=10,
                        use_proxy=None,
                    )
                    token, expires_in, expires_at = self._extract_token(response)
                    if token:
                        self._store_token(token, expires_in=expires_in, expires_at=expires_at, kind="preauth")
                        return token
                except HttpStatusError as exc:
                    if exc.status != 404:
                        raise
                return self.enroll_anonymous()
            body = self._device_proof()
            headers = {}
            current_token = str(token_data.get("token") or "").strip()
            if current_token:
                headers["Authorization"] = f"Bearer {current_token}"
            try:
                response = self._request_json(
                    "POST",
                    self._telemetry_token_url(),
                    body=body,
                    headers=headers,
                    timeout=10,
                    use_proxy=None,
                )
            except HttpStatusError as exc:
                if exc.status in (401, 403, 404):
                    return self.enroll_anonymous()
                raise
            token, expires_in, expires_at = self._extract_token(response)
            if token:
                kind = str(token_data.get("kind") or "preauth")
                self._store_token(token, expires_in=expires_in, expires_at=expires_at, kind=kind)
            return self._state.get("telemetry", {}).get("token", "")

    def ensure_telemetry_token(self, min_validity=None):
        with self._lock:
            token_data = self._state.get("telemetry", {})
            if self._token_valid(token_data, min_validity=min_validity):
                return token_data.get("token", "")
        return self.refresh_telemetry_token(force=False)

    def bind_identity(self, access_token):
        with self._lock:
            token = str(access_token or "").strip()
            if not token:
                return ""
            if not self._state.get("enroll_id"):
                self.enroll_anonymous()
            payload = self._device_proof()
            try:
                response = self._request_json(
                    "POST",
                    self._bind_url(),
                    body=payload,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                    use_proxy=None,
                )
                user_token, expires_in, expires_at = self._extract_token(response)
            except HttpStatusError as exc:
                if exc.status != 404:
                    raise
                telemetry = self._state.get("telemetry", {})
                user_token = str(telemetry.get("token") or "").strip()
                expires_in = None
                expires_at = telemetry.get("expires_at")
            if user_token:
                self._store_token(user_token, expires_in=expires_in, expires_at=expires_at, kind="user")
            return self._state.get("telemetry", {}).get("token", "")

    def rebind_required(self):
        return bool(self._state.get("needs_rebind", False))

    def telemetry_kind(self):
        data = self._state.get("telemetry", {})
        if isinstance(data, dict):
            return str(data.get("kind") or "")
        return ""

    def _telemetry_endpoint(self):
        config_data = self._state.get("bootstrap_config", {})
        if isinstance(config_data, dict):
            settings = config_data.get("config", config_data)
            if isinstance(settings, dict):
                endpoint = str(settings.get("telemetryEndpoint") or "").strip()
                if endpoint:
                    return endpoint
        return self._default_telemetry_endpoint()

    def _enqueue(self, payload, reason):
        items = self._queue_load()
        now = self._effective_now()
        items.append(
            {
                "payload": payload,
                "created_at": now,
                "expires_at": now + self._queue_ttl_seconds,
                "next_attempt_at": now,
                "attempts": 0,
                "last_reason": str(reason or ""),
            }
        )
        self._queue_save(items)

    def _purge_queue(self, items):
        now = self._effective_now()
        return [item for item in items if float(item.get("expires_at") or 0) > now]

    def _schedule_retry(self, item, reason):
        attempts = int(item.get("attempts") or 0) + 1
        base_backoff = min(300, (2 ** min(attempts, 8)))
        # Add ±10% jitter to avoid thundering herd when many clients retry simultaneously
        jitter = random.uniform(-0.1, 0.1) * base_backoff
        backoff = base_backoff + jitter
        item["attempts"] = attempts
        item["last_reason"] = str(reason or "")
        item["next_attempt_at"] = self._effective_now() + backoff
        return item

    def _http_post_trace(self, payload, token):
        endpoint = self._telemetry_endpoint()
        if not endpoint:
            raise SecureFlowError("telemetry endpoint is empty")
        headers = {
            "Content-Type": _CONTENT_TYPE_JSON,
            "Authorization": f"Bearer {token}",
            "X-Client-UUID": self.plugin_uuid,
        }
        body = _json_bytes(payload)
        status, response_headers, response_body = self._http_call(
            method="POST",
            url=endpoint,
            headers=headers,
            body=body,
            timeout=5,
            use_proxy=True,
        )
        self._update_clock_skew(response_headers if isinstance(response_headers, dict) else {})
        if int(status) < 200 or int(status) >= 300:
            text = response_body.decode("utf-8") if isinstance(response_body, (bytes, bytearray)) else str(response_body or "")
            raise HttpStatusError(status=status, body=text, headers=response_headers)

    def _drain_queue_locked(self):
        items = self._purge_queue(self._queue_load())
        now = self._effective_now()
        if not items:
            self._queue_save(items)
            return
        new_items = []
        for item in items:
            if float(item.get("next_attempt_at") or 0) > now:
                new_items.append(item)
                continue
            try:
                token = self.ensure_telemetry_token(min_validity=30)
                if not token:
                    new_items.append(self._schedule_retry(item, "no_token"))
                    continue
                self._http_post_trace(item.get("payload"), token)
            except HttpStatusError as exc:
                if exc.status in (401, 403):
                    try:
                        token = self.refresh_telemetry_token(force=True)
                        if token:
                            self._http_post_trace(item.get("payload"), token)
                            continue
                    except (SecureFlowError, OSError) as refresh_exc:
                        self._log(f"[SECURE] queue drain token refresh failed: {self._safe_log_error(refresh_exc)}")
                new_items.append(self._schedule_retry(item, f"http_{exc.status}"))
            except Exception as exc:
                new_items.append(self._schedule_retry(item, self._safe_log_error(exc)))
        self._queue_save(self._purge_queue(new_items))

    def send_trace(self, payload):
        with self._lock:
            if not isinstance(payload, dict):
                return False
            try:
                if not self._state.get("bootstrap_config"):
                    try:
                        self.fetch_bootstrap_config()
                    except (SecureFlowError, OSError) as cfg_exc:
                        self._log(f"[SECURE] bootstrap config fetch failed: {self._safe_log_error(cfg_exc)}")
                token = self.ensure_telemetry_token(min_validity=30)
                if not token:
                    self._enqueue(payload, "no_token")
                    return True
                self._http_post_trace(payload, token)
                self._drain_queue_locked()
                return True
            except HttpStatusError as exc:
                if exc.status in (401, 403):
                    try:
                        token = self.refresh_telemetry_token(force=True)
                        if token:
                            self._http_post_trace(payload, token)
                            self._drain_queue_locked()
                            return True
                    except (SecureFlowError, OSError) as refresh_exc:
                        self._log(f"[SECURE] send_trace token refresh failed: {self._safe_log_error(refresh_exc)}")
                    kind = str(self._state.get("telemetry", {}).get("kind") or "")
                    if kind == "user":
                        self._state["needs_rebind"] = True
                        self._save_state()
                self._enqueue(payload, f"http_{exc.status}")
                return True
            except Exception as exc:
                self._enqueue(payload, self._safe_log_error(exc))
                return True
