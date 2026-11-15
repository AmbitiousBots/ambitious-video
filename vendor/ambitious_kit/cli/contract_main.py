from __future__ import annotations

import sys

from ambitious_kit.contract import main


def contract_main() -> int:
    return main(sys.argv[1:])
