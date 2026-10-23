#!/usr/bin/env python3
"""Pack the fuxi firmware volume and device tree in an Android boot image v3 container.

Device-local mirror of the uefi-pong scripts/build-bootimg.py: it reuses UniXNU's
compile_shim, then appends the stock fuxi DTB as-is and calls mkbootimg directly with
the header version from Resources/Configs/fuxi.toml (v3). It does not go through
`unixnu bootimg`, so neither its v0-only verification nor its dtb_node patching runs.
The live fuxi.dtb already carries /reserved-memory/splash_region; not patching it
again avoids the FDT_ERR_EXISTS failure the legacy bootimg node patch produces.
"""
import argparse
import gzip
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unixnu-root', type=Path, default=root.parent / 'UniXNU')
    parser.add_argument('--ramdisk', type=Path)
    parser.add_argument('--fd', type=Path)
    parser.add_argument('--output-dir', type=Path, default=root / 'out/fuxi/boot')
    args = parser.parse_args()

    sys.path.insert(0, str(args.unixnu_root / 'scripts'))
    from unixnu.bootimg import compile_shim

    config = tomllib.loads((root / 'Resources/Configs/fuxi.toml').read_text())
    base = config['uefi_fd']['base']
    size = config['uefi_fd']['size']
    header_version = config['boot_image']['header_version']
    requires_header = config['boot_shim']['requires_kernel_header']

    fd = args.fd or root / 'Build/fuxiPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd'
    dtb = root / 'Resources/DTBs/fuxi.dtb'
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    image = output / 'fuxi-unixnu-uefi.img'
    ramdisk = args.ramdisk.read_bytes() if args.ramdisk else b''
    firmware = fd.read_bytes()

    with tempfile.TemporaryDirectory(prefix='fuxi-bootimg-') as temporary:
        scratch = Path(temporary)
        shim = compile_shim(root, base, size, requires_header, scratch)
        (scratch / 'kernel').write_bytes(gzip.compress(shim + firmware, mtime=0) + dtb.read_bytes())
        (scratch / 'ramdisk').write_bytes(ramdisk)
        subprocess.run([
            sys.executable, str(root / 'Resources/Scripts/mkbootimg.py'),
            '--header_version', str(header_version), '--kernel', str(scratch / 'kernel'),
            '--ramdisk', str(scratch / 'ramdisk'), '-o', str(image),
        ], check=True)

    print(image)


if __name__ == '__main__':
    main()
