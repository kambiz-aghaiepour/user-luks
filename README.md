# user-luks

Manage a LUKS-encrypted disk image **without root**, using `libguestfs`
(guestfish). Everything runs in a user-space QEMU appliance owned by your own
account — no `sudo`, no kernel `dm-crypt`, no loop devices, no `/dev/mapper`
on the host.

```
$ user-luks.py --create ~/secure.img --size 20
Enter key or passphrase ("key"):
created /home/kambiz/secure.img (20 GB, LUKS+ext4); workspace: /data
```

## Features

- `--create` — create a sparse LUKS image, `mkfs.ext4` it, create the
  `/data` workspace, close it. Size prompted interactively (default **10 GB**)
  or fixed with `--size N` (GB).
- `--ls` — `ls -l`-style listing of the workspace inside the image.
- `--get` — copy file(s) **out of** the image (cp-like, multiple sources ok).
- `--put` — copy file(s) **into** the image (cp-like, multiple sources ok).
- `--rm`, `--mkdir`, `--rmdir` — manage files/directories in the image.
- `-h` / `--help`.

The LUKS passphrase is prompted on the terminal for every image operation
(once per run where the image must be unlocked).

## Requirements

- Python 2.7 or 3.x (RHEL 7 ships 2.7; Fedora 3.14).
- `libguestfs-tools` (provides `guestfish`):
  - RHEL/CentOS 7: `sudo yum install libguestfs-tools`
  - Fedora / RHEL 9+: `sudo dnf install libguestfs-tools`
- `truncate` (coreutils) for `--create`.
- ~256 MB of free RAM for the QEMU appliance.

The script self-checks: it refuses to run with a clear message if
`guestfish` (or `truncate` for create) is missing, and reports non-zero
`guestfish` exits.

## Installation

```bash
mkdir -p ~/bin
cp user-luks.py ~/bin/
chmod +x ~/bin/user-luks.py
export PATH=$HOME/bin:$PATH        # add to ~/.bashrc
```

## Usage

```
user-luks.py --create PATH [--size N]      # create image (default 10 GB)
user-luks.py -i PATH --ls [PATH]             # list workspace or given PATH (ls -la, hidden files included: file or dir)
user-luks.py -i PATH --get SRC... DEST     # image  -> host (cp-like)
user-luks.py -i PATH --put SRC... DEST     # host   -> image (cp-like)
user-luks.py -i PATH --rm PATH...          # delete file(s)
user-luks.py -i PATH --mkdir DIR...        # create dir(s)
user-luks.py -i PATH --rmdir DIR...        # remove dir(s)
```

### Examples

```bash
# Create a 20 GB encrypted container
user-luks.py --create ~/secure.img --size 20

# List its contents (ls -la style, hidden files included)
user-luks.py -i ~/secure.img --ls

# List another location - a directory or a single file
user-luks.py -i ~/secure.img --ls /data/archives
user-luks.py -i ~/secure.img --ls /data/notes.txt

# Put several files into the container root
user-luks.py -i ~/secure.img --put ./notes.txt ./todo.txt /

# Get files out (multiple sources; destination must be an existing dir)
user-luks.py -i ~/secure.img --get notes.txt todo.txt /tmp/restore/

# Or rename a single file while extracting
user-luks.py -i ~/secure.img --get /notes.txt /tmp/notes-copy.txt

# Manage
user-luks.py -i ~/secure.img --mkdir archives
user-luks.py -i ~/secure.img --put ./backup.tar.gz /archives
user-luks.py -i ~/secure.img --rm notes.txt
user-luks.py -i ~/secure.img --rmdir archives
```

`--size` is in gigabytes; pressing Enter at the create prompt accepts the
default of 10.

## Image layout

- Format: standard **LUKS** (kernel-agnostic; version-dependent defaults,
  tested with LUKS1) containing an **ext4** filesystem.
- The working directory inside the image is **`/data`** (created by
  `--create`). All user paths are relative to it:
  - `notes.txt` and `/notes.txt` both map to `/data/notes.txt`
  - `/` (or `/data`) as a `--put` destination = the container root
- A freshly created image lists as an empty directory (no `lost+found`
  noise — it lives above `/data`).

## How it works

1. The script generates a short guestfish script and runs
   `guestfish --format=raw -a IMAGE -f SCRIPT`.
2. guestfish boots a small Linux appliance (QEMU, as your user) and inside it:
   `luks-format` / `luks-open` (with your passphrase), `mkfs`, `mount`,
   file operations, then `umount` and `luks-close`.
3. guestfish prompts for the passphrase on your controlling terminal — it
   is never stored or echoed.

### Portability notes

- **`--format=raw` is required.** Newer QEMU (>= 7, e.g. Fedora's QEMU 10)
  auto-detects the LUKS header on a `-drive` and refuses to attach it without
  a `key-secret`. Forcing `raw` makes the image a plain block device, and the
  appliance does the LUKS handling. RHEL 7's QEMU 1.5.3 is unaffected but the
  flag is harmless there and supported by guestfish 1.40+.
- **Cross-distro tested:** images created on RHEL 7 (guestfish 1.40.2) were
  copied to Fedora 44 (guestfish 1.60.1, QEMU 10) and `--ls`/`--put`/`--get`
  behaved identically, with byte-identical content. The image is portable in
  both directions.
- The script sets `LIBGUESTFS_BACKEND=direct` and `LIBGUESTFS_MEMSIZE=256`
  by default (overridable via environment). On RHEL 7 with constrained RAM
  the libvirt backend fails to allocate memory — direct + 256 MB is the
  working combination. 256 MB is also the hard minimum guestfish accepts.
- Sparse images transfer at full logical size: use `scp -C` (zeros compress)
  or `rsync -aS` to keep them sparse.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `too small value for memsize (must be at least 256)` | `LIBGUESTFS_MEMSIZE` below the hard minimum. Use 256. |
| `could not create appliance through libvirt ... Cannot set up guest memory` | Host RAM pressure; use `LIBGUESTFS_BACKEND=direct` (script default) and/or free memory. |
| `guestfish: error: expecting a device name` (delete this) | Only relevant to interactive libguestfs use; the script always uses the guestfish API path. |
| QEMU: `Parameter 'key-secret' is required for cipher` | Running an old copy of the script without `--format=raw`. Update. |
| Passphrase prompt appears twice on `--create` | Expected: once for `luks-format`, once for `luks-open`. |

## License

Released under the **GNU General Public License v3.0** — see [LICENSE](LICENSE).
