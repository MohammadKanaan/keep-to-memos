#!/usr/bin/env python3
"""Rebuild the testdata/takeout-test.zip fixture from the Takeout directory.

Usage: python3 testdata/make_zip.py
"""
import pathlib
import shutil

TESTDATA = pathlib.Path(__file__).resolve().parent
shutil.make_archive(str(TESTDATA / "takeout-test"), "zip",
                    root_dir=TESTDATA, base_dir="Takeout")
print("built takeout-test.zip")
