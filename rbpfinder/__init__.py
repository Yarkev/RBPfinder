"""RBPfinder -- high-recall, interpretable receptor-binding protein candidates.

`__version__` is read from the installed distribution metadata rather than written here.
A hand-maintained string is a second place for the version to live, and the two disagree
the first time one of them is updated alone -- which matters because an agent reporting a
result is expected to say which version produced it.
"""
try:
    from importlib.metadata import PackageNotFoundError, version as _version
    try:
        __version__ = _version("rbpfinder")
    except PackageNotFoundError:
        # Running from a source checkout that was never installed. Honest about it
        # rather than guessing a number.
        __version__ = "0+unknown"
except ImportError:                                    # Python < 3.8
    __version__ = "0+unknown"

__all__ = ["__version__"]
