#!/usr/bin/env python3

import multiprocessing
import subprocess
import sys
import textwrap
from typing import Iterable, Union

from general_superstaq.check import check_utils


@check_utils.enable_exit_on_failure
def run(  # pylint: disable=missing-function-docstring
    *args: str,
    include: Union[str, Iterable[str]] = "*.py",
    exclude: Union[str, Iterable[str]] = (),
    silent: bool = False,
) -> int:
    """Runs pylint on the repository (formatting check).

    Args:
        *args: Command line arguments.
        include: Glob(s) indicating which tracked files to consider (e.g. "*.py").
        exclude: Glob(s) indicating which tracked files to skip (e.g. "*integration_test.py").
        silent: If True, restrict printing to warning and error messages.
    """

    parser = check_utils.get_file_parser()
    parser.description = textwrap.dedent(
        """
        Runs pylint on the repository (formatting check).
        NOTE: Only checks incrementally changed files by default.
        """
    )

    num_cores = max(multiprocessing.cpu_count() // 2, 1)

    # perform incremental check by default
    parser.set_defaults(revisions=[])
    parser.add_argument(
        "-a",
        "--all",
        action="store_const",
        const=None,
        dest="revisions",
        help="Run pylint on the entire repo.",
    )

    parser.add_argument(
        "-j",
        "--cores",
        type=int,
        default=num_cores,
        help="Number of cores to use for this test.",
    )

    parsed_args, args_to_pass = parser.parse_known_intermixed_args(args)
    files = check_utils.extract_files(parsed_args, include, exclude, silent)

    args_to_pass.append(f"-j{parsed_args.cores}")

    if files:
        return subprocess.call(["pylint", *files, *args_to_pass], cwd=check_utils.root_dir)

    return 0


if __name__ == "__main__":
    exit(run(*sys.argv[1:]))
