import unittest

from distrohop.platform_ import current_platform


class PlatformTests(unittest.TestCase):
    def test_supported_platforms(self):
        self.assertEqual(current_platform("Linux"), "linux")
        self.assertEqual(current_platform("Windows"), "windows")

    def test_unsupported_platform_fails_clearly(self):
        with self.assertRaisesRegex(RuntimeError, "não suportada"):
            current_platform("Darwin")
