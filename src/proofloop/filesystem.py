"""Project paths, candidate snapshots, file modes, and descriptor-relative evidence reads."""

import contextlib
import errno
import hashlib
import os
import sys
import unicodedata
import uuid
from pathlib import Path

from . import core
from .coordinator import digest, require


MAX_BYTES = 2 * 1024 * 1024
MAX_LOG_BYTES = 10 * 1024 * 1024


def checked_project_root(value):
    root = Path(value).expanduser().absolute()
    require(not root.is_symlink(), 'project root cannot be a symlink; use its canonical path')
    return core.project_root(str(root))


def _darwin_acl_api():
    import ctypes
    try:
        lib = ctypes.CDLL(None, use_errno=True)
        lib.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
        lib.acl_get_fd_np.restype = ctypes.c_void_p
        lib.acl_init.argtypes = [ctypes.c_int]
        lib.acl_init.restype = ctypes.c_void_p
        lib.acl_set_fd_np.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        lib.acl_set_fd_np.restype = ctypes.c_int
        lib.acl_free.argtypes = [ctypes.c_void_p]
        lib.acl_free.restype = ctypes.c_int
        return lib
    except (AttributeError, OSError) as exc:
        raise ValueError('private-run ACL support is unavailable on this host') from exc


def directory_has_acl(fd):
    """Fail closed when an extended or default ACL cannot be inspected."""
    if sys.platform == 'darwin':
        import ctypes
        lib = _darwin_acl_api()
        ctypes.set_errno(0)
        acl = lib.acl_get_fd_np(fd, 0x100)  # Darwin ACL_TYPE_EXTENDED.
        if not acl:
            error = ctypes.get_errno()
            if error == errno.ENOENT:
                return False
            raise OSError(error or errno.EIO, 'cannot inspect run directory ACL')
        lib.acl_free(acl)
        return True
    if sys.platform.startswith('linux') and hasattr(os, 'getxattr'):
        for name in ('system.posix_acl_access', 'system.posix_acl_default'):
            try:
                os.getxattr(fd, name)
                return True
            except OSError as exc:
                if exc.errno != errno.ENODATA:
                    raise
        return False
    raise ValueError('private-run ACL inspection is unsupported on this host')


def clear_new_directory_acl(fd):
    """Remove inherited ACLs only from a directory this run just created."""
    if sys.platform == 'darwin':
        import ctypes
        lib = _darwin_acl_api()
        acl = lib.acl_init(0)
        if not acl:
            raise OSError(ctypes.get_errno() or errno.EIO, 'cannot initialize empty run directory ACL')
        try:
            if lib.acl_set_fd_np(fd, acl, 0x100) != 0:
                raise OSError(ctypes.get_errno() or errno.EIO, 'cannot clear inherited run directory ACL')
        finally:
            lib.acl_free(acl)
        return
    if sys.platform.startswith('linux') and hasattr(os, 'removexattr'):
        for name in ('system.posix_acl_access', 'system.posix_acl_default'):
            try:
                os.removexattr(fd, name)
            except OSError as exc:
                if exc.errno != errno.ENODATA:
                    raise
        return
    raise ValueError('private-run ACL handling is unsupported on this host')


def private_run_directory(root, relative, *, include_created=False):
    """Create a private run boundary without altering an existing directory."""
    parts = Path(relative).parts
    require(len(parts) == 3 and parts[0] == '.proof' and parts[1] in ('tasks', 'agent-runs') and
            core.RUN_ID_RE.fullmatch(parts[2]), 'invalid private run directory')
    core.safe_write_path(root, '/'.join(parts[:-1]) + '/.boundary-probe-' + uuid.uuid4().hex)
    directory = root / relative
    created = False
    try:
        directory.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        pass
    require(not directory.is_symlink() and directory.is_dir(), 'invalid run evidence directory')
    require(getattr(os, 'O_DIRECTORY', 0) and getattr(os, 'O_NOFOLLOW', 0),
            'private-run descriptor checks are unsupported on this host')
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if created:
            clear_new_directory_acl(fd)
            os.fchmod(fd, 0o700)
        require(os.fstat(fd).st_mode & 0o077 == 0,
                'run evidence directory must be private; inspect existing permissions before changing them')
        require(not directory_has_acl(fd),
                'run evidence directory has an ACL; inspect existing permissions before changing them')
    finally:
        os.close(fd)
    return (directory, created) if include_created else directory


def normalized_name(value):
    return unicodedata.normalize('NFKC', value).casefold()


def relative_name(value, *, legacy=False):
    require(isinstance(value, str) and bool(value.strip()), 'relative path required')
    require(legacy or not any(ord(char) < 32 or ord(char) == 127 for char in value),
            'control characters in paths are unsupported')
    p = Path(value)
    require(not p.is_absolute() and p.parts and '..' not in p.parts and str(p) == value,
            'paths must be normalized and project-relative')
    first = p.parts[0] if legacy else normalized_name(p.parts[0])
    require(first not in ('.proof', '.git', '.codex'), 'control/runtime path is not an operational file')
    return value


def file_path(root, name, optional=False):
    relative_name(name)
    cursor = root
    for part in Path(name).parts:
        cursor /= part
        require(not cursor.is_symlink(), 'symlink in operational path')
    if optional and not cursor.exists():
        return cursor
    require(cursor.is_file(), 'operational file missing: ' + name)
    require(cursor.stat().st_size <= MAX_BYTES, 'operational file too large')
    return cursor


def plan_file(root, name):
    # Generated task plans are control files, never operational editable files.
    parts = Path(name).parts
    if len(parts) == 4 and parts[:2] == ('.proof', 'tasks') and parts[3] == 'plan.json':
        require(str(Path(name)) == name and core.RUN_ID_RE.fullmatch(parts[2]), 'invalid managed plan path')
        return core.safe_project_file(root, name)
    return file_path(root, name)


def file_map(root, names):
    result = {}
    for name in names:
        p = file_path(root, name, optional=True)
        result[name] = digest(p) if p.exists() else None
    return result


def file_modes(root, names):
    result = {}
    for name in names:
        path = file_path(root, name, optional=True)
        mode = path.stat().st_mode & 0o7777 if path.exists() else None
        require(mode is None or not mode & 0o7000, 'special permission bits are unsupported: ' + name)
        result[name] = mode
    return result


def directory_names(names):
    directories = {'.'}
    for name in names:
        parent = Path(name).parent
        while str(parent) != '.':
            directories.add(str(parent))
            parent = parent.parent
    return directories


def directory_modes(root, names):
    result = {}
    for name in sorted(directory_names(names)):
        cursor = root
        for part in (() if name == '.' else Path(name).parts):
            cursor = cursor / part
            require(not cursor.is_symlink(), 'symlink in directory path')
        require(cursor.is_dir(), 'declared file parent directory missing: ' + name)
        mode = cursor.stat().st_mode & 0o7777
        require(not mode & 0o7000, 'special directory permission bits are unsupported: ' + name)
        result[name] = mode
    return result


def prepare_directories(root, modes):
    for name in sorted(modes, key=lambda n: (len(Path(n).parts), n)):
        if name != '.':
            (root / name).mkdir(parents=True, exist_ok=True)


def set_directory_modes(root, modes):
    for name in sorted(modes, key=lambda n: (len(Path(n).parts), n), reverse=True):
        os.chmod(root if name == '.' else root / name, modes[name])


def tree_map(root):
    result = {}
    for p in sorted(root.rglob('*')):
        require(not p.is_symlink(), 'snapshot/evidence symlink rejected')
        if p.is_file():
            result[str(p.relative_to(root))] = digest(p)
    return result


def tree_state(root):
    """Freeze file bytes, modes and directories, including empty directories."""
    result = {'.': {'type': 'directory', 'mode': root.stat().st_mode & 0o7777}}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(), 'snapshot/evidence symlink rejected')
        name = str(path.relative_to(root))
        mode = path.stat().st_mode & 0o7777
        if path.is_dir():
            result[name] = {'type': 'directory', 'mode': mode}
        else:
            require(path.is_file(), 'unsupported snapshot entry: ' + name)
            result[name] = {'type': 'file', 'mode': mode, 'sha256': digest(path)}
    return result


def fingerprint(value, modes=None, directories=None):
    data = value if modes is None else {'files': value, 'modes': modes, 'directories': directories}
    return hashlib.sha256(core.canonical_json(data).encode()).hexdigest()


def evidence_path_parts(name):
    require(isinstance(name, str) and name and '\x00' not in name,
            'nonempty relative path required')
    parts = name.split('/')
    require(not Path(name).is_absolute() and
            all(part not in ('', '.', '..') for part in parts) and
            str(Path(name)) == name, 'path must be normalized and relative: ' + name)
    return tuple(parts)


def open_evidence_root(root):
    """Acquire one directory boundary for the entire observation."""
    import stat
    require(os.name == 'posix' and os.open in os.supports_dir_fd and
            all(getattr(os, flag, 0) for flag in
                ('O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK', 'O_CLOEXEC')),
            'status requires descriptor-relative nofollow opens')
    root = Path(root).expanduser().absolute()
    require(not root.is_symlink(), 'project root cannot be a symlink')
    # Parent aliases such as macOS /var are allowed outside the selected root.
    # Resolve only the parent, then open every canonical component atomically.
    # The root leaf itself must never be resolved through a symlink.
    boundary = root.parent.resolve(strict=True) / root.name
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC
    fd = os.open('/', flags)
    try:
        require(stat.S_ISDIR(os.fstat(fd).st_mode), 'directory required')
        for part in boundary.parts[1:]:
            next_fd = os.open(part, flags, dir_fd=fd)
            previous_fd, fd = fd, next_fd
            os.close(previous_fd)
            require(stat.S_ISDIR(os.fstat(fd).st_mode), 'project root must be a directory')
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_evidence_bytes(root_fd, name, optional=False, *, include_mode=False):
    """Read beneath the pinned root; never reopen a checked pathname."""
    import stat
    parts = evidence_path_parts(name)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    with contextlib.ExitStack() as handles:
        parent_fd = root_fd
        try:
            for part in parts[:-1]:
                parent_fd = os.open(part, flags | os.O_DIRECTORY, dir_fd=parent_fd)
                handles.callback(os.close, parent_fd)
                require(stat.S_ISDIR(os.fstat(parent_fd).st_mode),
                        'directory required: ' + name)
            # Nonblocking prevents a substituted FIFO from hanging before fstat.
            fd = os.open(parts[-1], flags | os.O_NONBLOCK, dir_fd=parent_fd)
            handles.callback(os.close, fd)
        except FileNotFoundError:
            if optional:
                return (None, None) if include_mode else None
            raise
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode), 'regular file required: ' + name)
        require(info.st_size <= MAX_LOG_BYTES, 'status evidence too large: ' + name)
        chunks = []
        size = 0
        while size <= MAX_LOG_BYTES:
            chunk = os.read(fd, min(65536, MAX_LOG_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        require(size <= MAX_LOG_BYTES, 'status evidence too large: ' + name)
        raw = b''.join(chunks)
        return (raw, info.st_mode & 0o7777) if include_mode else raw


def evidence_directory_mode(root_fd, name, *, include_acl=False):
    import stat
    if name == '.':
        mode = os.fstat(root_fd).st_mode & 0o7777
        return (mode, directory_has_acl(root_fd)) if include_acl else mode
    parts = evidence_path_parts(name)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC
    with contextlib.ExitStack() as handles:
        fd = root_fd
        for part in parts:
            fd = os.open(part, flags, dir_fd=fd)
            handles.callback(os.close, fd)
            require(stat.S_ISDIR(os.fstat(fd).st_mode), 'directory required: ' + name)
        mode = os.fstat(fd).st_mode & 0o7777
        return (mode, directory_has_acl(fd)) if include_acl else mode
