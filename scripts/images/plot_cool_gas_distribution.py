import argparse
import sys
from pathlib import Path

root_dir = Path(__file__).parents[2].resolve()
sys.path.insert(0, str(root_dir / "src"))

from library import scriptparse
from pipelines.images.cool_gas_images import (
    PlotCoolGasDistrFromFile,
    PlotCoolGasDistribution,
)


def main(args: argparse.Namespace) -> None:
    """Plot the cool gas distribution at redshift zero"""
    # set simulation to TNG-Cluster, to get the correct cool gas history
    # archive file path
    args.sim = "TNG-Cluster"

    # type flag, subdirectories, and other config changes
    type_flag = "cool_gas_distribution"

    pipeline_config = scriptparse.startup(
        args,
        "images",
        type_flag,
        figures_subdirectory=".",
        data_subdirectory=".",
        suppress_sim_name_in_files=False,
    )

    # update pipeline config here
    pipeline_config.update(
        {
            "n_bins": args.n_bins, "z_threshold": args.max_depth
        }
    )

    # select and build pipeline
    if args.from_file:
        pipeline = PlotCoolGasDistrFromFile(**pipeline_config)
    else:
        pipeline = PlotCoolGasDistribution(**pipeline_config)
    sys.exit(pipeline.run())


DESCRIPTION = """Plot the distribution of cool gas at redshift zero.

Script creates a 2D histogram of the projected cool gas surface density
of all gas in TNG-Cluster clusters. This includes both individual clusters
as well as an overall mean distribution. Only gas within a projection
depth specified by `--max-width` and within twice the virial radius R_200c
will be considered.
"""

if __name__ == "__main__":
    parser = scriptparse.BaseScriptParser(
        prog=f"python {Path(__file__).name}",
        description=DESCRIPTION,
        allowed_sims=["TNG-Cluster"],
    )
    # remove unnecessary args
    parser.remove_argument("sim")
    parser.remove_argument("to_file")
    parser.remove_argument("processes")

    # add new args
    parser.add_argument(
        "-nb",
        "--n-bins",
        help="Number of bins in x- and y-direction. Defaults to 100.",
        type=int,
        default=100,
        dest="n_bins",
    )
    parser.add_argument(
        "-z",
        "--max-depth",
        help=(
            "The maximum absolute z-value that will be considered in the "
            "plot. Equivalent to half the projection depth, i.e. half the "
            "thickness of the slice that will be projected onto the "
            "x-y-plane. Must be given in units of virial radii. Defaults to "
            "0.5, which is equivalent to a projection depth of 1 virial "
            "radius."
        ),
        type=float,
        default=1.0,
        dest="max_depth",
    )

    # parse arguments
    try:
        args_ = parser.parse_args()
        main(args_)
    except KeyboardInterrupt:
        print("\nExecution forcefully stopped.\n")
        sys.exit(1)
