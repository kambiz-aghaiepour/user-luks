#!/usr/bin/env python
"""user-luks.py - manage a LUKS-encrypted disk image without root (libguestfs).

Modes:
  user-luks.py --create PATH [--size N]     create LUKS+ext4 image (default 10 GB)
  user-luks.py -i PATH --ls                 list files in image (ls -l style)
  user-luks.py -i PATH --get SRC... DEST    copy file(s) from image to host
  user-luks.py -i PATH --put SRC... DEST    copy file(s) from host into image
  user-luks.py -i PATH --rm PATH...         delete file(s) in image
  user-luks.py -i PATH --mkdir DIR...       create dir(s) in image
  user-luks.py -i PATH --rmdir DIR...       remove dir(s) from image

All paths inside the image are relative to /data (the workspace created at
--create).  Use "/" as the --put destination to place files in the container
root.  LUKS passphrase prompts appear on the terminal.

Requirements: libguestfs-tools (guestfish, > libguestfs 1.30), coreutils
(truncate), ~256 MB free RAM for the appliance.
"""
import argparse
import fnmatch
import os
import subprocess
import sys
import tempfile

try:
    input = raw_input
except NameError:
    pass

GUESTFISH = "guestfish"
DATA_DIR = "/data"          # workspace directory inside the LUKS filesystem
BASE_ENV = dict(os.environ)
BASE_ENV.setdefault("LIBGUESTFS_BACKEND", "direct")
BASE_ENV.setdefault("LIBGUESTFS_MEMSIZE", "256")


def err(msg):
    sys.stderr.write("user-luks: error: %s\n" % msg)
    sys.exit(1)


def find_prog(name):
    for d in os.environ.get("PATH", "").split(":"):
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def check_requirements(need_truncate):
    """Ensure guestfish (libguestfs) and friends exist before doing anything."""
    gf = find_prog(GUESTFISH)
    if gf is None:
        err("guestfish not found - install libguestfs-tools "
            "(RHEL/CentOS: 'sudo yum install libguestfs-tools'; "
            "Fedora/RHEL9+: 'sudo dnf install libguestfs-tools')")
    # `guestfish --version` is instant (no appliance launch), good smoke test.
    try:
        p = subprocess.Popen([gf, "--version"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        out = p.communicate()[0]
        if p.returncode != 0:
            err("guestfish --version failed; is libguestfs installed correctly?")
    except OSError as e:
        err("cannot execute guestfish (%s)" % e)
    if need_truncate and find_prog("truncate") is None:
        err("'truncate' not found - install coreutils")


def real(p):
    return os.path.abspath(os.path.expanduser(p))


def q(s):
    if '"' in s:
        s = s.replace('"', '\\"')
    if "\\" in s:
        s = s.replace("\\", "\\\\")
    return '"' + s + '"'


def fs_path(p):
    """Map a user-supplied image path to an absolute path under DATA_DIR."""
    p = p.strip()
    if p == DATA_DIR or p.startswith(DATA_DIR + "/"):
        return p                       # already an absolute image path
    p = p.lstrip("/")
    if p == "" or p == "data":
        return DATA_DIR
    return DATA_DIR + "/" + p


def run_gf(lines, image, capture=False):
    script = "\n".join(lines) + "\n"
    fd, tmp = tempfile.mkstemp(prefix="user-luks-", suffix=".gf")
    try:
        os.write(fd, script.encode("utf-8"))
    finally:
        os.close(fd)
    rc = 1
    out = ""
    try:
        if capture:
            p = subprocess.Popen([GUESTFISH, "--format=raw", "-a", image, "-f", tmp],
                                 env=BASE_ENV, stdout=subprocess.PIPE)
            out, _ = p.communicate()
            rc = p.returncode
            if not isinstance(out, str):
                out = out.decode("utf-8", "replace")
        else:
            rc = subprocess.call([GUESTFISH, "--format=raw", "-a", image, "-f", tmp],
                                 env=BASE_ENV)
    except OSError as e:
        err("cannot run guestfish (%s)" % e)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if rc != 0:
        err("guestfish failed (exit %d) - see output above" % rc)
    return out


def session(ops, image):
    """Open the LUKS volume, mount it, run ops, then unmount and close."""
    run_gf(["run",
            "luks-open /dev/sda vol",
            "mount /dev/mapper/vol /"] + ops +
           ["umount /",
            "luks-close /dev/mapper/vol"], image)


def session_capture(ops, image):
    """Like session() but return guestfish's combined stdout (for listing)."""
    return run_gf(["run",
                   "luks-open /dev/sda vol",
                   "mount /dev/mapper/vol /"] + ops +
                  ["umount /",
                   "luks-close /dev/mapper/vol"], image, capture=True)


def has_glob(s):
    return any(c in s for c in "*?[")


def split_pattern(s):
    """Split an image path into (rel_dir, pattern); only the final component
    may contain wildcards."""
    s = s.strip()
    idx = s.rfind("/")
    if idx == -1:
        return "", s
    return s[:idx], s[idx + 1:]


def expand_glob(src, image):
    """Expand a shell-style wildcard source against the guest filesystem.
    Returns sorted matched absolute image paths.  Dotfiles are only matched
    when the pattern itself starts with a dot (shell semantics)."""
    rel_dir, pat = split_pattern(src)
    if has_glob(rel_dir):
        err("wildcards are only supported in the last path component: %s" % src)
    base = fs_path(rel_dir if rel_dir else "/")
    out = session_capture(["ls " + q(base)], image)
    names = [line.strip() for line in out.splitlines() if line.strip()]
    matches = [n for n in names
               if (pat.startswith(".") or not n.startswith("."))
               and fnmatch.fnmatch(n, pat)]
    if not matches:
        err("no files match %s in the image" % src)
    return sorted(base.rstrip("/") + "/" + n for n in matches)


def cmd_create(args):
    path = real(args.create)
    if os.path.exists(path):
        err("refusing to overwrite existing file: %s" % path)
    size = args.size
    if size is None:
        s = input("Size in GB [10]: ").strip()
        if s:
            try:
                size = int(s)
            except ValueError:
                err("invalid size: " + repr(s))
        else:
            size = 10
    if size < 1:
        err("size must be at least 1 GB")
    try:
        subprocess.check_call(["truncate", "-s", "%dG" % size, path])
    except OSError as e:
        err("cannot truncate (%s)" % e)
    run_gf(["run",
            "luks-format /dev/sda 0",
            "luks-open /dev/sda vol",
            "mkfs ext4 /dev/mapper/vol",
            "mount /dev/mapper/vol /",
            "mkdir /data",
            "umount /",
            "luks-close /dev/mapper/vol"], path)
    print("created %s (%d GB, LUKS+ext4); workspace: %s" % (path, size, DATA_DIR))


def cmd_ls(image, target):
    if not target:
        target = DATA_DIR
    # `ll` lists in 'ls -la' format (hidden files included) and also works
    # on a single file path.
    session(["ll " + q(fs_path(target))], image)


def cmd_get(args, image):
    srcs = args.paths[:-1]
    if not srcs:
        err("--get requires at least one source and a destination")
    dest = real(args.paths[-1])

    # Expand wildcard sources against the guest filesystem first.
    items = []          # (label, image_abs_path)
    wildcard_srcs = [s for s in srcs if has_glob(s)]
    plain_srcs = [s for s in srcs if not has_glob(s)]
    for s in plain_srcs:
        items.append((s, fs_path(s)))
    for s in wildcard_srcs:
        for m in expand_glob(s, image):     # errors if nothing matches
            items.append((s, m))

    multi = len(items) > 1
    if multi and not os.path.isdir(dest):
        err("with multiple sources the destination must be an existing directory")

    ops = []
    done = []
    for label, isrc in items:
        if os.path.isdir(dest):
            d = os.path.join(dest, os.path.basename(isrc))
        else:
            if multi:
                err("with multiple sources the destination must be a directory")
            d = dest
        ops.append("download %s %s" % (q(isrc), q(d)))
        done.append((label, isrc, d))
    session(ops, image)
    for label, isrc, d in done:
        print("got %s -> %s" % (isrc, d))


def cmd_put(args, image):
    srcs = args.paths[:-1]
    if not srcs:
        err("--put requires at least one source and a destination directory")
    destdir = fs_path(args.paths[-1]).rstrip("/")
    ops = []
    done = []
    for s in srcs:
        host = real(s)
        if not os.path.isfile(host):
            err("no such file: %s" % s)
        idest = destdir + "/" + os.path.basename(host)
        ops.append("upload %s %s" % (q(host), q(idest)))
        done.append((host, idest))
    session(ops, image)
    for host, idest in done:
        print("put %s -> %s" % (host, idest))


def cmd_rm(args, image):
    ops = ["rm " + q(fs_path(p)) for p in args.paths]
    session(ops, image)
    for p in args.paths:
        print("rm %s" % fs_path(p))


def cmd_mkdir(args, image):
    ops = ["mkdir " + q(fs_path(p)) for p in args.paths]
    session(ops, image)
    for p in args.paths:
        print("mkdir %s" % fs_path(p))


def cmd_rmdir(args, image):
    ops = ["rmdir " + q(fs_path(p)) for p in args.paths]
    session(ops, image)
    for p in args.paths:
        print("rmdir %s" % fs_path(p))


def main():
    ap = argparse.ArgumentParser(
        description="Manage a LUKS-encrypted disk image without root (via libguestfs).")
    ap.add_argument("-i", "--image", metavar="PATH",
                    help="LUKS image path (required for all actions except --create)")
    ap.add_argument("--create", metavar="PATH",
                    help="create a new LUKS image at PATH")
    ap.add_argument("--size", type=int, default=None, metavar="N",
                    help="image size in GB for --create (default: 10)")
    ap.add_argument("--ls", nargs="?", const="", default=None, metavar="PATH",
                    help="list the image (ls -la style, hidden files included); "
                         "optional PATH (default: the /data workspace)")
    ap.add_argument("--get", nargs="+", metavar="SRC",
                    help="--get SRC... DEST (copy from image to host)")
    ap.add_argument("--put", nargs="+", metavar="SRC",
                    help="--put SRC... DEST (copy from host into image)")
    ap.add_argument("--rm", nargs="+", metavar="PATH",
                    help="remove file(s) from the image")
    ap.add_argument("--mkdir", nargs="+", metavar="DIR",
                    help="create directory/ies in the image")
    ap.add_argument("--rmdir", nargs="+", metavar="DIR",
                    help="remove directory/ies from the image")
    args = ap.parse_args()

    actions = [(args.create is not None), (args.ls is not None),
               args.get is not None, args.put is not None,
               args.rm is not None, args.mkdir is not None,
               args.rmdir is not None]
    if sum(actions) != 1:
        ap.error("exactly one action required (use -h for help)")

    if args.create is not None:
        if args.size is not None and args.size < 1:
            ap.error("--size must be at least 1")
        check_requirements(need_truncate=True)
        cmd_create(args)
        return
    if args.size is not None:
        ap.error("--size is only valid with --create")
    if not args.image:
        ap.error("--image PATH is required (use -h for help)")
    check_requirements(need_truncate=False)
    image = real(args.image)
    if not os.path.isfile(image):
        err("image not found: %s" % image)

    if args.ls is not None:
        cmd_ls(image, args.ls)
    elif args.get is not None:
        args.paths = args.get
        cmd_get(args, image)
    elif args.put is not None:
        args.paths = args.put
        cmd_put(args, image)
    elif args.rm is not None:
        args.paths = args.rm
        cmd_rm(args, image)
    elif args.mkdir is not None:
        args.paths = args.mkdir
        cmd_mkdir(args, image)
    elif args.rmdir is not None:
        args.paths = args.rmdir
        cmd_rmdir(args, image)


if __name__ == "__main__":
    main()
