#!/usr/bin/env bash
set -euo pipefail

UEFI_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
WORKSPACE_ROOT=$(cd "$UEFI_ROOT/.." && pwd)
UNIXNU_ROOT=${UNIXNU_ROOT:-$WORKSPACE_ROOT/UniXNU}
DEVICE_ROOT=${DEVICE_ROOT:-$WORKSPACE_ROOT/unixnu-fuxi}

if [[ ! -x "$UNIXNU_ROOT/unixnu" ]]; then
  echo "UniXNU core checkout not found: $UNIXNU_ROOT" >&2
  echo "Set UNIXNU_ROOT to the UniXNU repository directory." >&2
  exit 2
fi

if [[ ! -f "$DEVICE_ROOT/board.toml" ]]; then
  echo "fuxi device repository not found: $DEVICE_ROOT" >&2
  echo "Set DEVICE_ROOT to the unixnu-fuxi repository directory." >&2
  exit 2
fi

export PYTHON_COMMAND=${PYTHON_COMMAND:-python3}
export PYTHONPATH="$UEFI_ROOT/Mu_Basecore/BaseTools/Source/Python${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$UEFI_ROOT/Mu_Basecore/BaseTools/BinWrappers/PosixLike:$UEFI_ROOT/Mu_Basecore/BaseTools/Source/C/bin:$PATH"

cd "$UNIXNU_ROOT"
python3 unixnu build firmware -D "$DEVICE_ROOT" --uefi "$UEFI_ROOT" \
  --edk2 "$UEFI_ROOT/Mu_Basecore" \
  --local-module "refer:edk2=$UEFI_ROOT/Mu_Basecore" \
  --out "$UEFI_ROOT/out" --target RELEASE -j "${JOBS:-8}"
