import json
import unittest

from core.api.sms_sender import (
    BAIZE_SMS_CODE_PARAMETER,
    BAIZE_SMS_SIGN_NAME,
    BAIZE_SMS_TEMPLATE_CODE,
    aliyun_verification_request_fields,
)


class AliyunSMSSenderTest(unittest.TestCase):
    def test_uses_approved_baize_verification_template_by_default(self):
        fields = aliyun_verification_request_fields(
            {}, "13800138000", "123456"
        )

        self.assertEqual(fields["phone_numbers"], "13800138000")
        self.assertEqual(fields["sign_name"], BAIZE_SMS_SIGN_NAME)
        self.assertEqual(fields["sign_name"], "燃力猫文化")
        self.assertEqual(fields["template_code"], BAIZE_SMS_TEMPLATE_CODE)
        self.assertEqual(fields["template_code"], "SMS_510440112")
        self.assertEqual(
            json.loads(fields["template_param"]),
            {BAIZE_SMS_CODE_PARAMETER: "123456"},
        )

    def test_explicit_template_configuration_remains_supported(self):
        fields = aliyun_verification_request_fields(
            {
                "sign_name": "测试签名",
                "template_code": "SMS_TEST",
                "code_parameter": "otp",
            },
            "13800138000",
            "654321",
        )

        self.assertEqual(fields["sign_name"], "测试签名")
        self.assertEqual(fields["template_code"], "SMS_TEST")
        self.assertEqual(json.loads(fields["template_param"]), {"otp": "654321"})


if __name__ == "__main__":
    unittest.main()
