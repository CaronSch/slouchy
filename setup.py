"""py2app setup for Slouchy.

Build with: python setup.py py2app
"""

import sys
sys.setrecursionlimit(5000)

import glob
from setuptools import setup

APP = ["menubar.py"]
DATA_FILES = [
    ("phrases/tier1_gentle", glob.glob("phrases/tier1_gentle/*.wav")),
    ("phrases/tier2_firm", glob.glob("phrases/tier2_firm/*.wav")),
    ("phrases/tier3_nuclear", glob.glob("phrases/tier3_nuclear/*.wav")),
    # Bundle only the heavy pose model used by runtime config.
    ("models", ["models/pose_landmarker_heavy.task"]),
    ("", ["icon.png", "menubar_icon.png"]),
]
OPTIONS = {
    "iconfile": "app.icns",
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "Slouchy",
        "CFBundleDisplayName": "Slouchy",
        "CFBundleIdentifier": "com.slouchy.app",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "NSCameraUsageDescription": (
            "Slouchy uses your camera to detect posture. "
            "Video is processed locally and never stored."
        ),
        "LSUIElement": True,  # menubar app, no dock icon
    },
    "packages": ["mediapipe", "cv2", "rumps", "packaging"],
    "includes": [
        "menubar",
        "posture",
        "escalation",
        "tracker",
        "audio",
        "config",
        "dashboard",
    ],
    # Keep release bundles lean by excluding test/dev-only modules.
    "excludes": [
        "test",
        "tests",
        "pytest",
        "setuptools",
        "pkg_resources",
        "setuptools.tests",
        "setuptools._distutils.tests",
        "numpy.tests",
        "numpy.testing",
        "numpy.f2py.tests",
        "numpy._core.tests",
        "numpy.random.tests",
        "numpy.linalg.tests",
        "numpy.lib.tests",
        "numpy.ma.tests",
        "numpy.matrixlib.tests",
        "numpy.polynomial.tests",
        "numpy.fft.tests",
        "numpy.typing.tests",
        "matplotlib.tests",
        "matplotlib.testing",
        "mpl_toolkits.tests",
        "mediapipe.tasks.python.test",
    ],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
)
