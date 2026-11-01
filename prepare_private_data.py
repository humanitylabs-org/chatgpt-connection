RECIPE_ROOT = '/data'
"""Create-only mount preparation; metadata-only validation runs without root/caps.

Generated into Compose by tools/inline-private-data.py. No credential bytes read.
"""
import os
import stat
import sys

DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
PATH_DIRECTORY = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def open_directory(path):
    # Resolve every component with nofollow, not only the last component.
    if not path.startswith('/') or path == '/' or path != os.path.normpath(path):
        raise ValueError('Private storage initialization refused.')
    parts = path[1:].split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise ValueError('Private storage initialization refused.')
    fd = os.open('/', PATH_DIRECTORY)
    try:
        for part in parts:
            child = os.open(part, PATH_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def prepare(path, uid, gid):
    """Only empty 0:0 roots change; existing runtime ownership is untouched."""
    handle = open_directory(path)
    try:
        before = os.fstat(handle)
        if (before.st_uid, before.st_gid) == (uid, gid):
            return
        if (before.st_uid, before.st_gid) != (0, 0):
            raise ValueError('Private storage initialization refused.')
        fd = os.open('.', DIRECTORY, dir_fd=handle)
        try:
            if os.listdir(fd):
                raise ValueError('Private storage initialization refused.')
            # No FOWNER required: set mode while this inode is still root-owned.
            original_mode = stat.S_IMODE(before.st_mode)
            os.fchmod(fd, 0o700)
            try:
                os.fchown(fd, uid, gid)
            except BaseException:
                # A failed handoff leaves the original empty root's mode intact.
                after = os.fstat(fd)
                if (after.st_uid, after.st_gid) == (0, 0):
                    os.fchmod(fd, original_mode)
                raise
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        os.close(handle)


def validate(path, uid, gid):
    """Run as exactly uid:gid, no added capabilities; never chmod/chown/read files."""
    if (os.geteuid(), os.getegid()) != (uid, gid):
        raise ValueError('Private storage validation refused.')
    handle = open_directory(path)
    try:
        device = os.fstat(handle).st_dev

        def inspect(fd, depth=0):
            if depth > 64:
                raise ValueError('Private storage validation refused.')
            info = os.fstat(fd)
            if ((info.st_uid, info.st_gid) != (uid, gid) or info.st_dev != device
                    or not stat.S_ISDIR(info.st_mode)):
                raise ValueError('Private storage validation refused.')
            directory = os.open('.', DIRECTORY, dir_fd=fd)
            try:
                for name in os.listdir(directory):
                    child = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=directory)
                    try:
                        item = os.fstat(child)
                        if ((item.st_uid, item.st_gid) != (uid, gid)
                                or item.st_dev != device):
                            raise ValueError('Private storage validation refused.')
                        if stat.S_ISDIR(item.st_mode):
                            inspect(child, depth + 1)
                        elif not stat.S_ISREG(item.st_mode) or item.st_nlink != 1:
                            raise ValueError('Private storage validation refused.')
                    finally:
                        os.close(child)
            finally:
                os.close(directory)

        inspect(handle)
    finally:
        os.close(handle)


def main():
    # Fixed recipe contract, not a caller-selected host path or UID fallback.
    path, uid, gid = RECIPE_ROOT, 1000, 1000
    try:
        if sys.argv[1:] == ['prepare']:
            prepare(path, uid, gid)
        elif sys.argv[1:] == ['validate']:
            validate(path, uid, gid)
        else:
            raise ValueError('Private storage initialization refused.')
    except (OSError, ValueError):
        raise SystemExit('Private storage initialization refused; ownership/migration review required.') from None


if __name__ == '__main__':
    main()
