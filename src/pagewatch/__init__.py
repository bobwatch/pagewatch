"""PageWatch: free and open-source website change monitoring."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

try:
    __version__ = _distribution_version("pagewatch")
except PackageNotFoundError:  # running from a source checkout without installation
    __version__ = "0.7.0"
