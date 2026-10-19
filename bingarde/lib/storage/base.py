"""The interface every storage backend implements.

The app never talks to a backend directly: ``bingarde.lib.storage`` (the
common layer) checks keys, computes checksums and ties writes and deletes to
the database transaction, then calls these methods. A key is a relative,
already checked path such as ``2026/10/p42/app.exe``.
"""
from abc import ABC, abstractmethod


class StorageError(Exception):
    """The backend cannot do what was asked (disk full, bucket unreachable, ...)."""


class NotFound(StorageError):
    """No file is stored under this key."""


class Storage(ABC):
    """One place where build files are kept."""

    # Name used in the ``storage`` setting.
    name = None

    @abstractmethod
    def reserve(self, key):
        """Create an empty file under ``key`` if the key is free.

        Must be atomic: of two requests reserving the same key, only one gets
        True. Returns False when the key is taken."""

    @abstractmethod
    def write(self, key, stream):
        """Store the bytes of ``stream`` (has ``read(n)``) under a reserved key.

        Returns the number of bytes written."""

    @abstractmethod
    def open(self, key):
        """A readable binary file-like object (``read(n)``, ``close()``).

        Raises NotFound when nothing is stored under ``key``."""

    @abstractmethod
    def exists(self, key):
        """True when a file is stored under ``key``."""

    @abstractmethod
    def delete(self, key):
        """Remove the file under ``key``; a missing file is not an error."""

    @abstractmethod
    def download(self, key, name, request):
        """A webob Response that sends the file to the browser or API client.

        ``name`` is the file name for the Content-Disposition header. Raises
        NotFound when nothing is stored under ``key``."""

    def usage(self):
        """(free bytes, total bytes) of the store, or None when it has no fixed size."""
        return None

    @abstractmethod
    def check(self):
        """Raise StorageError when the store cannot be used (for /health)."""
