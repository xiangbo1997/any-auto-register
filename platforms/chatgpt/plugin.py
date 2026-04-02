"""ChatGPT / Codex CLI 平台插件"""

import random
import string

from core.base_mailbox import BaseMailbox
from core.base_platform import Account, AccountStatus, BasePlatform, RegisterConfig
from core.registry import register


@register
class ChatGPTPlatform(BasePlatform):
    name = "chatgpt"
    display_name = "ChatGPT"
    version = "1.0.0"

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox

    def check_valid(self, account: Account) -> bool:
        try:
            from platforms.chatgpt.payment import check_subscription_status

            class _A:
                pass

            a = _A()
            extra = account.extra or {}
            a.access_token = extra.get("access_token") or account.token
            a.cookies = extra.get("cookies", "")
            status = check_subscription_status(a, proxy=self.config.proxy if self.config else None)
            return status not in ("expired", "invalid", "banned", None)
        except Exception:
            return False

    def register(self, email: str = None, password: str = None) -> Account:
        if not password:
            password = "".join(random.choices(string.ascii_letters + string.digits + "!@#$", k=16))

        proxy = self.config.proxy if self.config else None
        browser_mode = (self.config.executor_type if self.config else None) or "protocol"
        log_fn = getattr(self, "_log_fn", print)
        from platforms.chatgpt.register_v2 import RegistrationEngineV2 as RegistrationEngine

        max_retries = 3
        if self.config and getattr(self.config, "extra", None):
            try:
                max_retries = int((self.config.extra or {}).get("register_max_retries", 3) or 3)
            except Exception:
                max_retries = 3

        if self.mailbox:
            _mailbox = self.mailbox
            _fixed_email = email

            class GenericEmailService:
                service_type = type("ST", (), {"value": "custom_provider"})()

                def __init__(self):
                    self._acct = None
                    self._email = _fixed_email

                def create_email(self, config=None):
                    if self._email and self._acct and _fixed_email:
                        return {"email": self._email, "service_id": self._acct.account_id, "token": ""}
                    self._acct = _mailbox.get_email()
                    if not self._email:
                        self._email = self._acct.email
                    elif not _fixed_email:
                        self._email = self._acct.email
                    return {"email": self._email, "service_id": self._acct.account_id, "token": ""}

                def get_verification_code(
                    self,
                    email=None,
                    email_id=None,
                    timeout=120,
                    pattern=None,
                    otp_sent_at=None,
                    exclude_codes=None,
                ):
                    if not self._acct:
                        raise RuntimeError("邮箱账户尚未创建，无法获取验证码")
                    return _mailbox.wait_for_code(
                        self._acct,
                        keyword="",
                        timeout=timeout,
                        otp_sent_at=otp_sent_at,
                        exclude_codes=exclude_codes,
                    )

                def update_status(self, success, error=None):
                    pass

                @property
                def status(self):
                    return None

            engine = RegistrationEngine(
                email_service=GenericEmailService(),
                proxy_url=proxy,
                browser_mode=browser_mode,
                callback_logger=log_fn,
                max_retries=max_retries,
                extra_config=(self.config.extra or {}),
            )
            engine.email = email
            engine.password = password
        else:
            from core.base_mailbox import TempMailLolMailbox

            _tmail = TempMailLolMailbox(proxy=proxy)

            class TempMailEmailService:
                service_type = type("ST", (), {"value": "tempmail_lol"})()

                def create_email(self, config=None):
                    acct = _tmail.get_email()
                    self._acct = acct
                    return {"email": acct.email, "service_id": acct.account_id, "token": acct.account_id}

                def get_verification_code(
                    self,
                    email=None,
                    email_id=None,
                    timeout=120,
                    pattern=None,
                    otp_sent_at=None,
                    exclude_codes=None,
                ):
                    return _tmail.wait_for_code(
                        self._acct,
                        keyword="",
                        timeout=timeout,
                        otp_sent_at=otp_sent_at,
                        exclude_codes=exclude_codes,
                    )

                def update_status(self, success, error=None):
                    pass

                @property
                def status(self):
                    return None

            engine = RegistrationEngine(
                email_service=TempMailEmailService(),
                proxy_url=proxy,
                browser_mode=browser_mode,
                callback_logger=log_fn,
                max_retries=max_retries,
                extra_config=(self.config.extra or {}),
            )
            if email:
                engine.email = email
                engine.password = password

        result = engine.run()
        if not result or not result.success:
            raise RuntimeError(result.error_message if result else "注册失败")

        # Try PKCE token exchange so uploaded tokens work with Codex CLI API
        # (chatgpt.com/backend-api/codex/responses requires app_EMoamEEZ73f0CkXaXp7hrann tokens)
        _reg_email = result.email
        _reg_password = result.password or password
        if _reg_email and _reg_password:
            try:
                from platforms.chatgpt.oauth_pkce_client import OAuthPkceClient
                _pkce = OAuthPkceClient(proxy=proxy, log_fn=log_fn)
                _login_oauth = _pkce.login_after_register(_reg_email, _reg_password)
                _workspace_id = _pkce.extract_workspace_id()
                _continue_url = _pkce.select_workspace(_workspace_id)
                _pkce_tokens = _pkce.follow_redirects_and_exchange_token(_continue_url, _login_oauth)
                if _pkce_tokens and _pkce_tokens.get("access_token"):
                    log_fn("PKCE token exchange succeeded — using OAuth tokens for Codex compatibility")
                    result.access_token = _pkce_tokens["access_token"]
                    result.refresh_token = _pkce_tokens.get("refresh_token", result.refresh_token)
                    result.id_token = _pkce_tokens.get("id_token", result.id_token)
            except Exception as _pkce_err:
                log_fn(f"PKCE token exchange failed (falling back to session token): {_pkce_err}")

        return Account(
            platform="chatgpt",
            email=result.email,
            password=result.password or password,
            user_id=result.account_id,
            token=result.access_token,
            status=AccountStatus.REGISTERED,
            extra={
                "access_token": result.access_token,
                "refresh_token": result.refresh_token,
                "id_token": result.id_token,
                "session_token": result.session_token,
                "workspace_id": result.workspace_id,
                "account_id": result.account_id,
                "expired": result.expired,
                "last_refresh": result.last_refresh,
                "auth_file_complete": result.auth_file_complete,
            },
        )

    def get_platform_actions(self) -> list:
        return [
            {"id": "login", "label": "登录获取 Token", "params": []},
            {"id": "refresh_token", "label": "刷新 Token", "params": []},
            {
                "id": "payment_link",
                "label": "生成支付链接",
                "params": [
                    {"key": "country", "label": "地区", "type": "select", "options": ["US", "SG", "TR", "HK", "JP", "GB", "AU", "CA"]},
                    {"key": "plan", "label": "套餐", "type": "select", "options": ["plus", "team"]},
                ],
            },
            {
                "id": "upload_cpa",
                "label": "上传 CPA",
                "params": [
                    {"key": "api_url", "label": "CPA API URL", "type": "text"},
                    {"key": "api_key", "label": "CPA API Key", "type": "text"},
                ],
            },
            {
                "id": "upload_sub2api",
                "label": "上传 Sub2API",
                "params": [
                    {"key": "api_url", "label": "Sub2API API URL", "type": "text"},
                    {"key": "api_key", "label": "Sub2API API Key", "type": "text"},
                ],
            },
            {
                "id": "upload_tm",
                "label": "上传 Team Manager",
                "params": [
                    {"key": "api_url", "label": "TM API URL", "type": "text"},
                    {"key": "api_key", "label": "TM API Key", "type": "text"},
                ],
            },
            {
                "id": "upload_codex_proxy",
                "label": "上传 CodexProxy",
                "params": [
                    {"key": "api_url", "label": "API URL", "type": "text"},
                    {"key": "api_key", "label": "Admin Key", "type": "text"},
                ],
            },
        ]

    def execute_action(self, action_id: str, account: Account, params: dict) -> dict:
        proxy = self.config.proxy if self.config else None
        extra = account.extra or {}

        class _A:
            pass

        a = _A()
        a.email = account.email
        a.access_token = extra.get("access_token") or account.token
        a.refresh_token = extra.get("refresh_token", "")
        a.id_token = extra.get("id_token", "")
        a.session_token = extra.get("session_token", "")
        a.client_id = extra.get("client_id", "app_EMoamEEZ73f0CkXaXp7hrann")
        a.cookies = extra.get("cookies", "")
        a.account_id = extra.get("account_id", "")
        a.expired = extra.get("expired", "")
        a.last_refresh = extra.get("last_refresh", "")
        a.auth_file_complete = bool(extra.get("auth_file_complete", False))

        if action_id == "login":
            return self._do_login(account, a, proxy)

        if action_id == "refresh_token":
            from platforms.chatgpt.token_refresh import TokenRefreshManager

            manager = TokenRefreshManager(proxy_url=proxy)
            result = manager.refresh_account(a)
            if result.success:
                return {
                    "ok": True,
                    "data": {
                        "access_token": result.access_token,
                        "refresh_token": result.refresh_token,
                    },
                }
            return {"ok": False, "error": result.error_message}

        if action_id == "payment_link":
            from platforms.chatgpt.payment import generate_plus_link, generate_team_link

            plan = params.get("plan", "plus")
            country = params.get("country", "US")
            if plan == "plus":
                url = generate_plus_link(a, proxy=proxy, country=country)
            else:
                url = generate_team_link(
                    a,
                    workspace_name=params.get("workspace_name", "MyTeam"),
                    price_interval=params.get("price_interval", "month"),
                    seat_quantity=int(params.get("seat_quantity", 5) or 5),
                    proxy=proxy,
                    country=country,
                )
            return {"ok": bool(url), "data": {"url": url}}

        if action_id == "upload_cpa":
            from platforms.chatgpt.cpa_upload import generate_token_json, upload_to_cpa

            token_data = generate_token_json(a, allow_compat_id_token=True)
            ok, msg = upload_to_cpa(
                token_data,
                api_url=params.get("api_url"),
                api_key=params.get("api_key"),
            )
            return {"ok": ok, "data": msg}

        if action_id == "upload_sub2api":
            from platforms.chatgpt.sub2api_upload import upload_to_sub2api

            ok, msg = upload_to_sub2api(
                a,
                api_url=params.get("api_url"),
                api_key=params.get("api_key"),
            )
            return {"ok": ok, "data": msg}

        if action_id == "upload_tm":
            from platforms.chatgpt.cpa_upload import upload_to_team_manager

            ok, msg = upload_to_team_manager(
                a,
                api_url=params.get("api_url"),
                api_key=params.get("api_key"),
            )
            return {"ok": ok, "data": msg}

        if action_id == "upload_codex_proxy":
            upload_type = str(
                params.get("upload_type")
                or (self.config.extra or {}).get("codex_proxy_upload_type")
                or "at"
            ).strip().lower()

            if upload_type == "rt":
                from platforms.chatgpt.cpa_upload import upload_to_codex_proxy

                ok, msg = upload_to_codex_proxy(
                    a,
                    api_url=params.get("api_url"),
                    api_key=params.get("api_key"),
                )
            else:
                from platforms.chatgpt.cpa_upload import upload_at_to_codex_proxy

                ok, msg = upload_at_to_codex_proxy(
                    a,
                    api_url=params.get("api_url"),
                    api_key=params.get("api_key"),
                )
            return {"ok": ok, "data": msg}

        raise NotImplementedError(f"未知操作: {action_id}")

    def _do_login(self, account: "Account", codex_acc, proxy: str) -> dict:
        """
        用 email + password 走 ChatGPT OAuth 登录（复用注册流程的 OAuthClient），
        获取 access_token / refresh_token / id_token。
        若登录需要 OTP，自动用 Microsoft 凭证（extra.client_id / extra.refresh_token）
        从 Outlook Graph API 读取验证码。
        """
        import uuid
        from platforms.chatgpt.oauth_client import OAuthClient

        extra = account.extra or {}
        ms_client_id = extra.get("client_id", "")
        ms_refresh_token = extra.get("refresh_token", "")

        device_id = str(uuid.uuid4())
        cfg = self.config.extra if self.config and self.config.extra else {}

        logs = []

        # 构造 Outlook OTP 适配器（如有 Microsoft 凭证）
        skymail = None
        if ms_client_id and ms_refresh_token:
            skymail = _OutlookOTPAdapter(
                ms_client_id, ms_refresh_token, account.email, proxy,
                log_fn=lambda msg: logs.append(msg),
            )

        client = OAuthClient(
            config=cfg,
            proxy=proxy,
            verbose=False,
            browser_mode="protocol",
        )

        client._log = lambda msg, *_: logs.append(msg)

        tokens = client.login_and_get_tokens(
            email=account.email,
            password=account.password,
            device_id=device_id,
            impersonate="chrome110",
            skymail_client=skymail,
        )

        if not tokens or not tokens.get("access_token"):
            err = client.last_error or "登录失败，未获取到 access_token"
            return {"ok": False, "error": err, "logs": logs}

        # 构建 auth file metadata
        from platforms.chatgpt.register_v2 import RegistrationEngineV2
        auth_meta = RegistrationEngineV2._build_auth_file_metadata(tokens)

        data: dict = {
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token", ""),
            "id_token": tokens.get("id_token", ""),
            "account_id": auth_meta.get("account_id", ""),
            "expired": auth_meta.get("expired", ""),
            "last_refresh": auth_meta.get("last_refresh", ""),
            "auth_file_complete": bool(auth_meta.get("auth_file_complete", False)),
        }
        # 保留 Microsoft 凭证，防止被 ChatGPT 字段覆盖
        if ms_client_id:
            data["ms_client_id"] = ms_client_id
        if ms_refresh_token:
            data["ms_refresh_token"] = ms_refresh_token
        return {"ok": True, "data": data, "logs": logs}

    @staticmethod
    def _fetch_outlook_otp(client_id: str, refresh_token: str, proxy: str = None) -> str:
        """
        用 Microsoft refresh_token 换取带 Mail.Read 权限的 access_token，
        然后通过 Graph API 读取最新邮件中的 6 位 OTP。
        """
        import re
        import time
        from curl_cffi import requests as cffi_requests
        from core.proxy_utils import build_requests_proxy_config
        from platforms.chatgpt.constants import MICROSOFT_TOKEN_ENDPOINTS

        proxies = build_requests_proxy_config(proxy)
        session = cffi_requests.Session(proxies=proxies, impersonate="chrome")

        resp = session.post(
            MICROSOFT_TOKEN_ENDPOINTS["LIVE"],
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": "https://graph.microsoft.com/Mail.Read offline_access",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"获取 Outlook Mail.Read token 失败: HTTP {resp.status_code} {resp.text[:200]}")
        ms_token = resp.json().get("access_token")
        if not ms_token:
            raise RuntimeError("Microsoft token 响应中缺少 access_token")

        time.sleep(6)
        mail_resp = session.get(
            "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages"
            "?$top=5&$orderby=receivedDateTime+desc&$select=subject,bodyPreview,body",
            headers={"Authorization": f"Bearer {ms_token}", "Accept": "application/json"},
            timeout=30,
        )
        if mail_resp.status_code != 200:
            raise RuntimeError(f"Graph API 读取邮件失败: HTTP {mail_resp.status_code} {mail_resp.text[:200]}")

        for msg in mail_resp.json().get("value", []):
            text = msg.get("bodyPreview", "") + " " + (msg.get("body") or {}).get("content", "")
            match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
            if match:
                return match.group(1)
        return ""


class _OutlookOTPAdapter:
    """
    OAuthClient.login_and_get_tokens 的 skymail_client 适配器。
    通过 appleemail.top API 从 Outlook 收件箱读取 OTP 验证码。
    """

    def __init__(self, client_id: str, refresh_token: str, email: str, proxy: str = None, log_fn=None):
        self._client_id = client_id
        self._refresh_token = refresh_token
        self._email = email
        self._proxy = proxy
        self._used_codes: set = set()
        self._api_base = "https://www.appleemail.top"
        self._log = log_fn or (lambda msg: None)

    def fetch_emails(self, email=None) -> list:
        from curl_cffi import requests as cffi_requests
        from core.proxy_utils import build_requests_proxy_config

        target_email = email or self._email
        proxies = build_requests_proxy_config(self._proxy)
        session = cffi_requests.Session(proxies=proxies, impersonate="chrome")
        self._log(f"[AppleMail] 查询收件箱: {target_email}")
        try:
            resp = session.get(
                f"{self._api_base}/api/mail-new",
                params={
                    "refresh_token": self._refresh_token,
                    "client_id": self._client_id,
                    "email": target_email,
                    "mailbox": "INBOX",
                    "response_type": "json",
                },
                timeout=30,
            )
        except Exception as e:
            self._log(f"[AppleMail] 请求异常: {e}")
            return []
        self._log(f"[AppleMail] 响应 HTTP {resp.status_code}: {resp.text[:200]}")
        if resp.status_code != 200:
            return []
        data = resp.json()
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return data
        return []

    def extract_verification_code(self, content: str) -> str:
        import re
        match = re.search(r"(?<!\d)(\d{6})(?!\d)", content or "")
        return match.group(1) if match else ""

    def _read_inbox_otp(self, exclude_codes: set = None, after_ts: float = None) -> str:
        exclude = (exclude_codes or set()) | self._used_codes
        msgs = self.fetch_emails(self._email)
        for msg in msgs:
            content = (
                msg.get("bodyPreview") or msg.get("body") or
                msg.get("content") or msg.get("text") or ""
            )
            if isinstance(content, dict):
                content = content.get("content", "")
            code = self.extract_verification_code(str(content))
            if code and code not in exclude:
                return code
        return ""

    def wait_for_verification_code(
        self, email=None, timeout=90, otp_sent_at=None, exclude_codes=None
    ) -> str:
        import time
        wait_start = otp_sent_at if otp_sent_at else time.time()
        deadline = time.time() + timeout
        time.sleep(6)
        while time.time() < deadline:
            code = self._read_inbox_otp(exclude_codes, after_ts=wait_start - 30)
            if code:
                self._used_codes.add(code)
                return code
            time.sleep(5)
        return ""
