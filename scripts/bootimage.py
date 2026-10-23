"""Device-local bootimage command for fuxi.

Kept in the UEFI checkout instead of the UniXNU core. It imports the core
bootimg helpers and packs/verifies a header-v3/v4 boot image, tolerating
device-tree nodes that already exist in a live tree such as splash_region.
"""
import gzip
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path


HEADER_V3_BYTES = 1580
PAGE_V3 = 4096


def patch_dtb(dtb, nodes, scratch):
    """Add device-tree nodes the stock bootloader expects, without failing when
    a node already exists in the supplied tree."""
    if not nodes:
        return dtb
    fdtput, fdtget = tool('fdtput', 'brew install dtc'), tool('fdtget', 'brew install dtc')
    work = scratch / 'device.dtb'
    work.write_bytes(dtb)
    for node in nodes:
        created = subprocess.run([fdtput, '-c', str(work), node['path']],
                                 capture_output=True, text=True)
        if created.returncode and 'FDT_ERR_EXISTS' not in created.stdout + created.stderr:
            raise Error(f"fdtput -c {node['path']} failed: "
                        f"{(created.stdout + created.stderr).strip()}")
        for prop in node.get('props', []):
            values = prop['value'] if isinstance(prop['value'], list) else [prop['value']]
            kind = prop.get('type', 's')
            run([fdtput, '-t', kind, str(work), node['path'], prop['name'], *[str(v) for v in values]], capture=True)
            got = run([fdtget, '-t', kind, str(work), node['path'], prop['name']], capture=True).split()
            if kind in ('x', 'i', 'u') and [int(v, 16 if kind == 'x' else 10) for v in got] != [int(str(v), 0) for v in values]:
                raise Error(f'dtb node {node["path"]} property {prop["name"]} did not round-trip')
    return work.read_bytes()


def build(ctx, uefi, fd, dtb, ramdisk=None, output=None):
    log('bootimage')
    image, base, size, requires_header, config = settings(ctx, uefi)
    header_version = int(image.get('header_version', 0))
    if header_version < 3:
        raise Error(f'bootimage packs header v3 and newer, this device asks v{header_version}; use "unixnu bootimg"')

    mkbootimg = uefi / 'Resources/Scripts/mkbootimg.py'
    if not mkbootimg.is_file():
        raise Error(f'{mkbootimg} missing: --uefi must be a Mu-Silicium checkout')
    for path, what in ((fd, 'firmware volume (--fd)'), (dtb, 'device tree (--dtb)')):
        if not path or not Path(path).is_file():
            raise Error(f'{what} not found: {path}')
    firmware = Path(fd).read_bytes()
    if base is None or size is None:
        raise Error('the Mu-Silicium device config has no [uefi_fd] base and size')
    if len(firmware) != size:
        raise Error(f'firmware volume is {len(firmware):#x} bytes, the device config says {size:#x}')

    out = Path(output) if output else ctx.work / 'boot/boot-v3.img'
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix='unixnu-bootimage-') as scratch_name:
        scratch = Path(scratch_name)
        shim = compile_shim(uefi, base, size, requires_header, scratch)
        tree = patch_dtb(Path(dtb).read_bytes(), ctx.board.get('bootimg', {}).get('dtb_node', []), scratch)
        if tree[:4] != b'\xd0\x0d\xfe\xed' or struct.unpack_from('>I', tree, 4)[0] != len(tree):
            raise Error('invalid compiled device tree')
        combined = shim + firmware
        kernel = gzip.compress(combined, mtime=0) + tree
        kernel_path = scratch / 'Image.gz-dtb'
        kernel_path.write_bytes(kernel)
        if ramdisk:
            ramdisk_path = Path(ramdisk)
            if not ramdisk_path.is_file():
                raise Error(f'ramdisk not found: {ramdisk_path}')
        else:
            ramdisk_path = scratch / 'ramdisk'
            ramdisk_path.write_bytes(b'')
        command = [sys.executable, str(mkbootimg), '--kernel', str(kernel_path), '--ramdisk', str(ramdisk_path), '-o', str(out)]
        for key in MKBOOTIMG_FLAGS:
            if key in image:
                command += [f'--{key}', str(image[key])]
        run(command, capture=True)
        report = verify(out, combined, tree, ramdisk_path, image)

    report.update(image_sha256=sha256_file(out), image_bytes=out.stat().st_size, fd_sha256=sha256_file(fd),
                  dtb_sha256=sha256_file(dtb), shim_bytes=len(shim), hardware_tested=False)
    (out.parent / 'bootimage.json').write_text(json.dumps(report, indent=2) + '\n')
    log(f'bootimage: {out} ({out.stat().st_size} bytes)')
    return out


def verify(path, combined, tree, ramdisk_path, image):
    """Unpack a v3-or-newer image and compare every part with its input."""
    data = Path(path).read_bytes()
    if data[:8] != b'ANDROID!':
        raise Error('not an Android boot image')
    header_version = int(image.get('header_version', 0))
    kernel_size, ramdisk_size, _, header_size = struct.unpack_from('<4I', data, 8)
    version = struct.unpack_from('<I', data, 40)[0]
    if version != header_version or header_size != HEADER_V3_BYTES:
        raise Error(f'boot image header is v{version} ({header_size} bytes), '
                    f'asked v{header_version} ({HEADER_V3_BYTES} bytes)')
    page = PAGE_V3
    kernel = data[page:page + kernel_size]
    start = page + -(-kernel_size // page) * page
    ramdisk = data[start:start + ramdisk_size]
    unpacker = zlib.decompressobj(16 + zlib.MAX_WBITS)
    if unpacker.decompress(kernel) != combined or not unpacker.eof or unpacker.unused_data != tree:
        raise Error('gzip, firmware and appended device tree do not round-trip')
    if ramdisk != Path(ramdisk_path).read_bytes():
        raise Error('ramdisk bytes differ')
    return {'kernel_sha256': hashlib.sha256(kernel).hexdigest(), 'ramdisk_bytes': ramdisk_size,
            'header_version': version}

def main():
    import argparse
    from types import SimpleNamespace

    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unixnu-root', type=Path, default=root.parent / 'UniXNU')
    parser.add_argument('--device', type=Path, default=root.parent / 'unixnu-fuxi')
    parser.add_argument('--uefi', type=Path, default=root)
    parser.add_argument('--fd', type=Path, default=root / 'Build/fuxiPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd')
    parser.add_argument('--dtb', type=Path, default=root / 'Resources/DTBs/fuxi.dtb')
    parser.add_argument('--ramdisk', type=Path)
    parser.add_argument('--output', '-o', type=Path, default=root / 'out/fuxi/boot/boot-v3.img')
    args = parser.parse_args()

    sys.path.insert(0, str(args.unixnu_root / 'scripts'))
    from unixnu.bootimg import MKBOOTIMG_FLAGS, compile_shim, settings
    from unixnu.config import load_toml
    from unixnu.util import Error, log, run, sha256_file, tool

    for name, value in (
        ('MKBOOTIMG_FLAGS', MKBOOTIMG_FLAGS),
        ('compile_shim', compile_shim),
        ('settings', settings),
        ('Error', Error),
        ('log', log),
        ('run', run),
        ('sha256_file', sha256_file),
        ('tool', tool),
    ):
        globals()[name] = value

    ctx = SimpleNamespace(
        board=load_toml(args.device / 'board.toml'),
        work=args.output.parent,
    )
    build(ctx, args.uefi, args.fd, args.dtb, args.ramdisk, args.output)


if __name__ == '__main__':
    main()
