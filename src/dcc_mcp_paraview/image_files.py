"""Linux directory-fd anchored staging for bounded ImageData artifacts."""

import hashlib
import os
import re
import stat
from pathlib import Path

if __package__:
    from .image_data import MAX_OUTPUT_BYTES, ImageDataError
else:
    from image_data import MAX_OUTPUT_BYTES, ImageDataError


class ImageOutput:
    """Pin the verified parent directory and clean only two request-owned stage names.

    The sidecar retains its own directory descriptor until the owned host stops,
    so cancellation/SIGKILL need not rely on the native process running finally.
    No published destination is ever removed by cleanup.
    """

    def __init__(self, root, output, token, expected_directory=None):
        root, output = Path(root), Path(output)
        output = root / output if not output.is_absolute() else output
        try:
            relative = output.relative_to(root)
        except ValueError:
            raise ImageDataError("Output must stay inside the workspace", "path_denied") from None
        if not relative.parts or ".." in relative.parts or output.suffix.lower() != ".vti":
            raise ImageDataError("A workspace-confined new .vti path is required", "path_denied")
        if not re.fullmatch(r"[a-f0-9]{32}", token):
            raise ImageDataError("Invalid internal staging identity")
        self.fd = None
        self.files = {}
        self.path = output
        self.names = (".dcc-mcp-image-" + token + ".native.vti", ".dcc-mcp-image-" + token + ".vti")
        self._cleanup_enabled = False
        descriptor = None
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptor = os.open(str(root), flags)
            for part in relative.parts[:-1]:
                child = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            self.fd = descriptor
            descriptor = None
            metadata = os.fstat(self.fd)
            self.directory_identity = {"device": metadata.st_dev, "inode": metadata.st_ino}
            if expected_directory is not None:
                if not valid_directory_identity(expected_directory):
                    raise ImageDataError("Invalid internal output directory identity")
                if self.directory_identity != expected_directory:
                    raise ImageDataError("Output directory changed after sidecar preflight", "path_denied")
            for name in (output.name, *self.names):
                try:
                    os.stat(name, dir_fd=self.fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise ImageDataError("Output or request staging path already exists", "output_exists")
            self._cleanup_enabled = True
        except (OSError, ImageDataError) as error:
            if descriptor is not None:
                os.close(descriptor)
            self.close()
            if isinstance(error, ImageDataError):
                raise
            raise ImageDataError("Output directory is missing, inaccessible or a symlink", "path_denied") from None

    def stage_path(self, native=False):
        name = self.names[0 if native else 1]
        if name in self.files:
            return Path("/proc/self/fd/%d" % self.files[name])
        return Path("/proc/self/fd/%d/%s" % (self.fd, name))

    def reserve(self, native=False):
        name = self.names[0 if native else 1]
        descriptor = os.open(name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        self.files[name] = descriptor
        return self.stage_path(native)

    def publish(self, verifier):
        staged = self.stage_path()
        metadata = staged.stat()
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_OUTPUT_BYTES:
            raise ImageDataError("Host did not produce a bounded nonempty VTI", "verification_failed")
        evidence = verifier(staged)
        try:
            # Link the verified open file, never a possibly replaced staging pathname.
            os.link(str(staged), self.path.name, dst_dir_fd=self.fd, follow_symlinks=True)
        except FileExistsError:
            raise ImageDataError("Output was created concurrently; existing file preserved", "output_exists") from None
        return {
            "path": str(self.path),
            "bytes": metadata.st_size,
            "sha256": hashlib.sha256(staged.read_bytes()).hexdigest(),
            **evidence,
        }

    def cleanup(self):
        if self.fd is None or not self._cleanup_enabled:
            return
        for name in self.names:
            try:
                metadata = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
                if stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.getuid():
                    os.unlink(name, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def close(self):
        if self.fd is not None:
            try:
                self.cleanup()
            finally:
                for descriptor in self.files.values():
                    os.close(descriptor)
                self.files.clear()
                os.close(self.fd)
                self.fd = None


def valid_directory_identity(value):
    return (
        isinstance(value, dict)
        and set(value) == {"device", "inode"}
        and type(value["device"]) is int
        and 0 <= value["device"] < 2**64
        and type(value["inode"]) is int
        and 0 < value["inode"] < 2**64
    )
