"""Read-only artifact resolution for vogue-workflow/0.1 (conventions section 8).

``artifact://<relative-path>`` maps to ``<root>/<relative-path>``. The reader
refuses malformed URIs, empty/``.``/``..`` segments and symlinks at any depth,
verifies ``size_bytes`` and the SHA-256 of the exact bytes, and fails closed.
"""
from __future__ import annotations

import errno
from hashlib import sha256
import os
from pathlib import Path
import re
import stat

from .reviews import ReviewError

UPSTREAM_ARTIFACT_ROOT_ENV = 'VOGUE_UPSTREAM_ARTIFACT_ROOT'
# Conventions section 6: a retained response larger than this is 413.
MAX_ARTIFACT_BYTES = 16_777_216
_URI = re.compile(r'artifact://[A-Za-z0-9_./-]+')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_CLOEXEC = getattr(os, 'O_CLOEXEC', 0)
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | _CLOEXEC
_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)


def _unavailable():
    return ReviewError('ARTIFACT_UNAVAILABLE', 'Artifact bytes cannot be read from the artifact mount.', 503)


def _symlink_refused():
    return ReviewError('INVALID_ARTIFACT_PATH', 'Artifact paths may not traverse symlinks.', 422)


class DirectoryArtifacts:
    """Resolve artifact references inside one read-only directory tree."""

    def __init__(self, root):
        # The operator-configured mount point itself may sit below symlinked
        # system paths; everything below it is walked without following links.
        self.root = Path(root).resolve()

    def __call__(self, artifact):
        if not isinstance(artifact, dict):
            raise ReviewError('INVALID_ARTIFACT_URI', 'Artifact reference must be an object.', 422)
        uri, size, digest = artifact.get('uri'), artifact.get('size_bytes'), artifact.get('sha256')
        if not isinstance(uri, str) or not _URI.fullmatch(uri):
            raise ReviewError('INVALID_ARTIFACT_URI', 'Artifact URI is malformed.', 422)
        parts = uri[len('artifact://'):].split('/')
        if any(part in ('', '.', '..') for part in parts):
            raise ReviewError('INVALID_ARTIFACT_PATH', 'Artifact URI must name a plain relative path.', 422)
        if (not isinstance(size, int) or isinstance(size, bool) or size < 1
                or not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
            raise ReviewError('INVALID_ARTIFACT_URI', 'Artifact size or SHA-256 is malformed.', 422)
        if size > MAX_ARTIFACT_BYTES:
            raise ReviewError('ARTIFACT_TOO_LARGE', 'Artifact exceeds the retained response limit.', 413)
        raw = self._read(parts, size)
        if len(raw) != size:
            raise ReviewError('ARTIFACT_SIZE_MISMATCH', 'Artifact size does not match its reference.', 422)
        if sha256(raw).hexdigest() != digest:
            raise ReviewError('ARTIFACT_HASH_MISMATCH', 'Artifact SHA-256 does not match its reference.', 422)
        return raw

    def _read(self, parts, size):
        try:
            directory = os.open(self.root, _DIRECTORY_FLAGS)
        except OSError:
            raise _unavailable() from None
        try:
            for name in parts[:-1]:
                self._refuse_symlink(name, directory)
                child = os.open(name, _DIRECTORY_FLAGS | _NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            self._refuse_symlink(parts[-1], directory)
            # O_NOFOLLOW also closes the lstat/open race on the final component.
            descriptor = os.open(parts[-1], os.O_RDONLY | _NOFOLLOW | _CLOEXEC, dir_fd=directory)
            with os.fdopen(descriptor, 'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise _unavailable()
                if info.st_size != size:
                    raise ReviewError('ARTIFACT_SIZE_MISMATCH', 'Artifact size does not match its reference.', 422)
                return handle.read(size + 1)
        except ReviewError:
            raise
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise _symlink_refused() from None
            raise _unavailable() from None
        finally:
            os.close(directory)

    @staticmethod
    def _refuse_symlink(name, directory):
        if stat.S_ISLNK(os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode):
            raise _symlink_refused()


def require_artifact_reader(artifact):
    """Fail-closed reader used when no artifact root is configured."""
    raise ReviewError('ARTIFACT_READER_REQUIRED',
                      'Retained raw artifacts cannot be verified: ' + UPSTREAM_ARTIFACT_ROOT_ENV + ' is not configured.', 503)


def artifact_reader_from_env(environ=None):
    """Return a DirectoryArtifacts reader for the configured root, or the fail-closed reader."""
    root = (os.environ if environ is None else environ).get(UPSTREAM_ARTIFACT_ROOT_ENV, '').strip()
    return DirectoryArtifacts(root) if root else require_artifact_reader
