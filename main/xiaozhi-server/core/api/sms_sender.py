import asyncio
import json
import os


BAIZE_SMS_SIGN_NAME = "燃力猫文化"
BAIZE_SMS_TEMPLATE_CODE = "SMS_510440112"
BAIZE_SMS_CODE_PARAMETER = "code"


class SMSConfigurationError(RuntimeError):
    """Raised when the SMS provider is intentionally unavailable."""


class SMSProviderError(RuntimeError):
    def __init__(self, code: str, public_message: str):
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message

    def __str__(self) -> str:
        return self.public_message


class AliyunSMSSender:
    """Small adapter around Alibaba Cloud's Dysmsapi 2017-05-25 V2 SDK."""

    def __init__(self, config: dict):
        self.config = config

    async def send_verification_code(self, phone: str, code: str) -> str | None:
        return await asyncio.to_thread(self._send_verification_code, phone, code)

    def _send_verification_code(self, phone: str, code: str) -> str | None:
        settings = self.config.get("app_mvp", {}).get("sms", {}) or {}
        if not settings.get("enabled", False):
            raise SMSConfigurationError("短信服务尚未启用")
        if str(settings.get("provider", "aliyun")).lower() != "aliyun":
            raise SMSConfigurationError("短信服务提供商配置无效")

        access_key_id = _secret_from_environment_or_config(
            settings,
            "access_key_id",
            "access_key_id_env",
            "ALIBABA_CLOUD_ACCESS_KEY_ID",
        )
        access_key_secret = _secret_from_environment_or_config(
            settings,
            "access_key_secret",
            "access_key_secret_env",
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
        )
        request_fields = aliyun_verification_request_fields(settings, phone, code)
        if not all((access_key_id, access_key_secret)):
            raise SMSConfigurationError("短信服务缺少 AccessKey 配置")

        try:
            from alibabacloud_dysmsapi20170525.client import (
                Client as Dysmsapi20170525Client,
            )
            from alibabacloud_dysmsapi20170525 import (
                models as dysmsapi_20170525_models,
            )
            from alibabacloud_tea_openapi import models as open_api_models
            from alibabacloud_tea_util import models as util_models
        except ImportError as error:
            raise SMSConfigurationError("服务器未安装阿里云短信 SDK") from error

        try:
            open_api_config = open_api_models.Config(
                access_key_id=access_key_id,
                access_key_secret=access_key_secret,
            )
            open_api_config.endpoint = str(
                settings.get("endpoint", "dysmsapi.aliyuncs.com")
            ).strip()
            client = Dysmsapi20170525Client(open_api_config)
            request = dysmsapi_20170525_models.SendSmsRequest(**request_fields)
            runtime = util_models.RuntimeOptions(
                connect_timeout=2000,
                read_timeout=5000,
                autoretry=False,
            )
            response = client.send_sms_with_options(request, runtime)
        except Exception as error:
            provider_code = str(
                getattr(error, "code", None)
                or getattr(error, "error_code", None)
                or "SDK_ERROR"
            )
            raise SMSProviderError(
                provider_code,
                _public_message_for_provider_code(provider_code),
            ) from error

        body = getattr(response, "body", None)
        provider_code = str(getattr(body, "code", "") or "")
        if provider_code != "OK":
            raise SMSProviderError(
                provider_code or "UNKNOWN_ERROR",
                _public_message_for_provider_code(provider_code),
            )
        return getattr(body, "request_id", None) or getattr(body, "biz_id", None)


def aliyun_verification_request_fields(
    settings: dict, phone: str, code: str
) -> dict[str, str]:
    """Build the approved Baize template fields used by Alibaba Cloud SendSms."""
    sign_name = str(settings.get("sign_name") or BAIZE_SMS_SIGN_NAME).strip()
    template_code = str(
        settings.get("template_code") or BAIZE_SMS_TEMPLATE_CODE
    ).strip()
    code_parameter = str(
        settings.get("code_parameter") or BAIZE_SMS_CODE_PARAMETER
    ).strip()
    if not all((sign_name, template_code, code_parameter)):
        raise SMSConfigurationError("短信服务缺少签名或模板配置")
    return {
        "phone_numbers": phone,
        "sign_name": sign_name,
        "template_code": template_code,
        "template_param": json.dumps(
            {code_parameter: code},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def _secret_from_environment_or_config(
    settings: dict,
    config_key: str,
    environment_key_setting: str,
    default_environment_key: str,
) -> str:
    environment_key = (
        str(settings.get(environment_key_setting, default_environment_key)).strip()
        or default_environment_key
    )
    return (
        os.getenv(environment_key, "").strip()
        or str(settings.get(config_key, "")).strip()
    )


def _public_message_for_provider_code(code: str) -> str:
    normalized = (code or "").upper()
    if "BUSINESS_LIMIT_CONTROL" in normalized:
        return "短信发送过于频繁，请稍后再试"
    if "MOBILE_NUMBER_ILLEGAL" in normalized:
        return "手机号格式不正确"
    if "AMOUNT_NOT_ENOUGH" in normalized or "OUT_OF_SERVICE" in normalized:
        return "短信服务暂不可用，请稍后再试"
    return "短信发送失败，请稍后再试"
