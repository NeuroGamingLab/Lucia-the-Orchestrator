"""Dave IT Guy — Deploy AI stacks with one command."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dave-it-guy")
except PackageNotFoundError:
    __version__ = "0.0.0.dev0"
