"""Bounded product file views of the native workspace's host bind mount.

Directory descriptors and O_NOFOLLOW reject symlink traversal, including a
concurrent replacement of a parent directory. Uploads are inert byte streams.
"""
from __future__ import annotations

import asyncio
import codecs
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from uuid import uuid4

from fastapi import UploadFile

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
PREVIEW_BYTES = 256 * 1024
BINARY_PREVIEW_BYTES = 4096
MAX_DIRECTORY_ENTRIES = 500
CHUNK_BYTES = 64 * 1024


def relative_parts(path: str, *, allow_root: bool = False) -> tuple[str, ...]:
    if allow_root and path in {"", "."}:
        return ()
    parts = PurePosixPath(path).parts
    if (not path or path.startswith("/") or "\\" in path or not parts
            or any(part in {".", ".."} or part.startswith(".") for part in parts)
            or any(ord(char) < 32 or ord(char) == 127 for char in path)
            or str(PurePosixPath(path)) != path):
        raise ValueError("Path must name a visible file within this workspace")
    return parts


@contextmanager
def directory(root: Path, parts: tuple[str, ...] = ()):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def open_file(root: Path, path: str):
    parts = relative_parts(path)
    with directory(root, parts[:-1]) as parent:
        opened = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    stream = os.fdopen(opened, "rb")
    info = os.fstat(stream.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        stream.close()
        raise FileNotFoundError("Not a regular workspace file")
    return stream, info


def remove_file(root: Path, path: str) -> None:
    parts = relative_parts(path)
    with directory(root, parts[:-1]) as parent:
        info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise FileNotFoundError("Not a regular workspace file")
        os.unlink(parts[-1], dir_fd=parent)


def list_directory(root: Path, path: str) -> dict:
    parts = relative_parts(path, allow_root=True)
    entries, visited, truncated = [], 0, False
    with directory(root, parts) as fd:
        with os.scandir(fd) as children:
            for child in children:
                visited += 1
                if visited > MAX_DIRECTORY_ENTRIES * 10 or len(entries) >= MAX_DIRECTORY_ENTRIES:
                    truncated = True
                    break
                if child.name.startswith("."):
                    continue
                try:
                    info = child.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if not stat.S_ISDIR(info.st_mode) and not (stat.S_ISREG(info.st_mode) and info.st_nlink == 1):
                    continue
                entries.append({"name": child.name, "path": "/".join((*parts, child.name)),
                                "is_dir": stat.S_ISDIR(info.st_mode), "size_bytes": info.st_size})
    entries.sort(key=lambda item: (not item["is_dir"], item["name"].casefold()))
    return {"path": "/".join(parts), "entries": entries, "truncated": truncated}


def preview_file(root: Path, path: str) -> dict:
    stream, before = open_file(root, path)
    with stream:
        data = stream.read(PREVIEW_BYTES + 1)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("File changed while reading; retry after writing finishes")
    truncated = len(data) > PREVIEW_BYTES
    data = data[:PREVIEW_BYTES]
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(data, final=not truncated)
        binary = "\x00" in text or any(ord(char) < 32 and char not in "\t\n\r" for char in text)
    except UnicodeError:
        text, binary = "", True
    result = {"name": PurePosixPath(path).name, "path": path, "size": before.st_size,
              "content": "" if binary else text, "encoding": "binary" if binary else "utf-8",
              "truncated": truncated}
    if binary:
        data = data[:BINARY_PREVIEW_BYTES]
        lines = []
        for offset in range(0, len(data), 16):
            chunk = data[offset:offset + 16]
            readable = "".join(chr(value) if 32 <= value < 127 else "." for value in chunk)
            lines.append(f"{offset:08x}  {chunk.hex(' '):47}  {readable}")
        result["hex"] = "\n".join(lines)
        result["truncated"] = before.st_size > len(data)
    return result


async def upload_sample(root: Path, upload: UploadFile) -> dict:
    name = upload.filename or "sample.bin"
    parts = relative_parts(name)
    if len(parts) != 1 or len(name.encode("utf-8")) > 180:
        raise ValueError("Upload filename must be a basename of at most 180 UTF-8 bytes")
    sample_id = "sample-" + uuid4().hex
    filename = f"{sample_id}_{name}"
    temporary = f".{uuid4().hex}.part"
    with directory(root) as root_fd:
        try:
            os.mkdir("samples", 0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
    with directory(root, ("samples",)) as parent:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        size, digest = 0, hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as stream:
                while chunk := await upload.read(CHUNK_BYTES):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise OverflowError(f"Upload exceeds the {MAX_UPLOAD_BYTES} byte limit")
                    digest.update(chunk)
                    await asyncio.to_thread(stream.write, chunk)
                await asyncio.to_thread(stream.flush)
                await asyncio.to_thread(os.fsync, stream.fileno())
            # link is atomic and cannot replace a pre-existing file; the random
            # storage name also preserves repeated same-name uploads.
            os.link(temporary, filename, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        finally:
            os.unlink(temporary, dir_fd=parent)
            await upload.close()
    return {"sample_id": sample_id, "name": name, "sample_path": f"samples/{filename}", "reverse_sample_path": filename,
            "sha256": digest.hexdigest(), "size": size}


async def stream_file(stream):
    try:
        while chunk := await asyncio.to_thread(stream.read, CHUNK_BYTES):
            yield chunk
    finally:
        stream.close()


def discover_legacy_samples(root: Path) -> tuple[list[dict], bool]:
    """Catalog a bounded existing samples directory once, without copying bytes.

    At most 100 files / 256 MiB are hashed; symlinks, special files, hidden
    paths and changing files are excluded. Nested folders remain in the file
    browser and make the import explicitly incomplete.
    """
    try:
        listing = list_directory(root, "samples")
    except FileNotFoundError:
        return [], False
    except OSError:
        return [], True
    samples, total, truncated = [], 0, listing["truncated"]
    for entry in listing["entries"]:
        if entry["is_dir"] or len(samples) >= 100 or entry["size_bytes"] > MAX_UPLOAD_BYTES or total + entry["size_bytes"] > 256 * 1024 * 1024:
            truncated = True
            continue
        try:
            stream, before = open_file(root, entry["path"])
            digest, read = hashlib.sha256(), 0
            with stream:
                while chunk := stream.read(CHUNK_BYTES):
                    read += len(chunk)
                    if read > MAX_UPLOAD_BYTES:
                        raise OverflowError("Changing file exceeded import limit")
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                truncated = True
                continue
            total += read
            samples.append({"name": entry["name"], "sample_path": entry["path"],
                            "size": read, "sha256": digest.hexdigest(),
                            "created_at": datetime.fromtimestamp(before.st_mtime, timezone.utc).isoformat()})
        except (OSError, ValueError, OverflowError):
            truncated = True
    return samples, truncated
