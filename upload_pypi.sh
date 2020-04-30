#!/usr/bin/env bash
# Upload the latest release to PyPI. See https://pypi.org/project/av1transcoder/

set -o errexit
set -e

# Delete old builds. This is done to avoid
# re-uploading them when publishing a new release.
rm -r build dist

# Build the new version
python3 setup.py sdist bdist_wheel

# Upload to PyPI
python3 -m twine upload dist/*
