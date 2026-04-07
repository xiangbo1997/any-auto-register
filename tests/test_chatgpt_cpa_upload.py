import base64
import json
import sys
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock

if "curl_cffi" not in sys.modules:
    sys.modules["curl_cffi"] = SimpleNamespace(
        requests=SimpleNamespace(post=MagicMock()),
        CurlMime=MagicMock(),
    )

if "sqlmodel" not in sys.modules:
    class _SQLModel:
        def __init_subclass__(cls, **kwargs):
            return super().__init_subclass__()

    def _field(*args, **kwargs):
        return None

    def _create_engine(*args, **kwargs):
        return object()

    def _select(*args, **kwargs):
        return None

    sys.modules["sqlmodel"] = SimpleNamespace(
        Session=object,
        SQLModel=_SQLModel,
        Field=_field,
        create_engine=_create_engine,
        select=_select,
    )

from platforms.chatgpt.cpa_upload import generate_token_json
from services.chatgpt_sync import upload_chatgpt_account_to_cpa


def _jwt(payload: dict) -> str:
    header = {"alg": "none", "typ": "JWT"}

    def _b64(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{_b64(header)}.{_b64(payload)}."


class GenerateTokenJsonTests(unittest.TestCase):
    def test_prefers_saved_auth_file_metadata(self):
        access_token = _jwt(
            {
                "exp": 1775567774,
                "https://api.openai.com/auth": {"chatgpt_account_id": "acct-from-token"},
            }
        )
        account = SimpleNamespace(
            email="tester@example.com",
            access_token=access_token,
            refresh_token="refresh-token",
            id_token="real-id-token",
            account_id="acct-from-extra",
            expired="2026-04-07T21:16:14+08:00",
            last_refresh="2026-03-28T21:16:13+08:00",
        )

        token_data = generate_token_json(account)

        self.assertEqual(token_data["account_id"], "acct-from-extra")
        self.assertEqual(token_data["expired"], "2026-04-07T21:16:14+08:00")
        self.assertEqual(token_data["last_refresh"], "2026-03-28T21:16:13+08:00")
        self.assertEqual(token_data["id_token"], "real-id-token")

    def test_falls_back_to_jwt_for_missing_metadata(self):
        access_token = _jwt(
            {
                "exp": 1775567774,
                "https://api.openai.com/auth": {"chatgpt_account_id": "acct-from-token"},
            }
        )
        account = SimpleNamespace(
            email="tester@example.com",
            access_token=access_token,
            refresh_token="refresh-token",
            id_token="real-id-token",
        )

        token_data = generate_token_json(account)

        self.assertEqual(token_data["account_id"], "acct-from-token")
        self.assertEqual(token_data["refresh_token"], "refresh-token")
        self.assertTrue(token_data["expired"].endswith("+08:00"))
        self.assertTrue(token_data["last_refresh"].endswith("+08:00"))

    def test_does_not_build_compat_id_token_by_default(self):
        access_token = _jwt(
            {
                "exp": 1775567774,
                "https://api.openai.com/auth": {"chatgpt_account_id": "acct-from-token"},
            }
        )
        account = SimpleNamespace(
            email="tester@example.com",
            access_token=access_token,
            refresh_token="refresh-token",
            id_token="",
        )

        token_data = generate_token_json(account)

        self.assertEqual(token_data["id_token"], "")


class UploadChatGPTAccountToCpaTests(unittest.TestCase):
    def test_rejects_account_without_refresh_token(self):
        account = SimpleNamespace(
            email="tester@example.com",
            token="access-token",
            extra={
                "access_token": "access-token",
                "id_token": "id-token",
                "auth_file_complete": True,
            },
        )

        ok, msg = upload_chatgpt_account_to_cpa(account)

        self.assertFalse(ok)
        self.assertIn("refresh_token", msg)

    def test_rejects_account_without_real_id_token(self):
        account = SimpleNamespace(
            email="tester@example.com",
            token="access-token",
            extra={
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "auth_file_complete": True,
            },
        )

        ok, msg = upload_chatgpt_account_to_cpa(account)

        self.assertFalse(ok)
        self.assertIn("id_token", msg)

    def test_uploads_when_auth_file_is_complete(self):
        account = SimpleNamespace(
            email="tester@example.com",
            token="access-token",
            extra={
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "id_token": "id-token",
                "account_id": "acct-1",
                "expired": "2026-04-07T21:16:14+08:00",
                "last_refresh": "2026-03-28T21:16:13+08:00",
                "auth_file_complete": True,
            },
        )

        with mock.patch("platforms.chatgpt.cpa_upload.upload_to_cpa", return_value=(True, "上传成功")) as mocked_upload:
            ok, msg = upload_chatgpt_account_to_cpa(account)

        self.assertTrue(ok)
        self.assertEqual(msg, "上传成功")
        mocked_upload.assert_called_once()
        token_data = mocked_upload.call_args.args[0]
        self.assertEqual(token_data["account_id"], "acct-1")
        self.assertEqual(token_data["id_token"], "id-token")


if __name__ == "__main__":
    unittest.main()
