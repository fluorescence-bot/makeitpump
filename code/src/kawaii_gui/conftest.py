"""
Pytest configuration and fixtures for RainMixer API tests.
"""

import sys
import types

# Stub webview module before any imports of api.py
if "webview" not in sys.modules:
    webview = types.ModuleType("webview")
    webview.FileDialog = types.SimpleNamespace(OPEN=1, SAVE=2)
    webview.OPEN_DIALOG = 1
    webview.SAVE_DIALOG = 2
    sys.modules["webview"] = webview

# Never drive the real pump from the test suite.
import hardware  # noqa: E402

hardware.FORCE_MOCK = True
