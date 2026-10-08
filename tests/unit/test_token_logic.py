"""
Tests for token/JWT logic and PKCE in MainJob.
No LibreOffice required — UNO modules are stubbed.
"""
import base64
import time
import unittest

from tests.stubs.uno_stubs import install, make_job, make_jwt

install()


class TestJwtPayload(unittest.TestCase):
    def setUp(self):
        self.job = make_job()

    def test_valid_jwt_returns_payload(self):
        token = make_jwt({"sub": "user1", "exp": 9999999999})
        payload = self.job._jwt_payload(token)
        self.assertEqual(payload["sub"], "user1")
        self.assertEqual(payload["exp"], 9999999999)

    def test_invalid_token_returns_empty_dict(self):
        self.assertEqual(self.job._jwt_payload("not.a.jwt.at.all.extra"), {})

    def test_empty_token_returns_empty_dict(self):
        self.assertEqual(self.job._jwt_payload(""), {})

    def test_single_segment_returns_empty_dict(self):
        self.assertEqual(self.job._jwt_payload("onlyone"), {})


class TestTokenIsExpired(unittest.TestCase):
    def setUp(self):
        self.job = make_job()

    def test_expired_token_returns_true(self):
        token = make_jwt({"exp": int(time.time()) - 100})
        self.assertTrue(self.job._token_is_expired(token, skew_seconds=0))

    def test_valid_token_returns_false(self):
        token = make_jwt({"exp": int(time.time()) + 3600})
        self.assertFalse(self.job._token_is_expired(token, skew_seconds=0))

    def test_missing_exp_claim_returns_false(self):
        # Token without "exp" should be treated as non-expired (not blocked)
        token = make_jwt({"sub": "user1"})
        self.assertFalse(self.job._token_is_expired(token))

    def test_skew_makes_future_token_appear_expired(self):
        # Token expires in 30s, skew=60 → should appear expired
        token = make_jwt({"exp": int(time.time()) + 30})
        self.assertTrue(self.job._token_is_expired(token, skew_seconds=60))

    def test_boundary_exact_expiry_is_expired(self):
        # Token exp == now + skew exactly → should be expired (>= boundary fix)
        skew = 60
        token = make_jwt({"exp": int(time.time()) + skew})
        # With skew=60, effective check is time.time() >= (exp - 60) = now → True
        self.assertTrue(self.job._token_is_expired(token, skew_seconds=skew))

    def test_non_numeric_exp_returns_false(self):
        token = make_jwt({"exp": "not-a-number"})
        self.assertFalse(self.job._token_is_expired(token))


class TestPkce(unittest.TestCase):
    def setUp(self):
        self.job = make_job()

    def test_verifier_is_base64url_without_padding(self):
        verifier = self.job._pkce_code_verifier()
        self.assertNotIn("=", verifier)
        self.assertNotIn("+", verifier)
        self.assertNotIn("/", verifier)

    def test_verifier_length_meets_rfc7636(self):
        # RFC 7636: verifier must be 43-128 chars
        verifier = self.job._pkce_code_verifier()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertLessEqual(len(verifier), 128)

    def test_verifier_entropy_96_bytes(self):
        # 96 bytes → 128 base64url chars (before stripping padding)
        verifier = self.job._pkce_code_verifier()
        self.assertEqual(len(verifier), 128)

    def test_verifier_uniqueness(self):
        v1 = self.job._pkce_code_verifier()
        v2 = self.job._pkce_code_verifier()
        self.assertNotEqual(v1, v2)

    def test_challenge_is_sha256_base64url(self):
        import hashlib
        verifier = self.job._pkce_code_verifier()
        challenge = self.job._pkce_code_challenge(verifier)
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        self.assertEqual(challenge, expected)


if __name__ == "__main__":
    unittest.main()
