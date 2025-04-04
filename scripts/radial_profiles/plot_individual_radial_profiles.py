import argparse
import sys
from pathlib import Path

import numpy as np

root_dir = Path(__file__).parents[2].resolve()
sys.path.insert(0, str(root_dir / "src"))

from library import scriptparse
from pipelines.radial_profiles.individuals import (
    IndividualProfilesFromFilePipeline,
    IndividualProfilesTNGClusterPipeline,
    IndividualRadialProfilePipeline,
)


def main(args: argparse.Namespace) -> None:
    """Create histograms of temperature distribution"""
    # paths
    if args.core_only:
        type_flag = f"{args.what}_core"  # prevent overwriting
    else:
        type_flag = args.what
    if not args.normalize:
        type_flag += "_absolute_dist"

    pipeline_config = scriptparse.startup(
        args,
        "radial_profiles",
        type_flag,
        with_virial_temperatures=False,
        figures_subdirectory="./individuals/",
        data_subdirectory=f"./individuals/{args.sim.replace('-', '_')}/",
    )

    # temperature bins is either the number of bins or the three regimes
    if args.what == "temperature":
        tbins = args.tbins
    else:
        tbins = np.array([0, 4.5, 5.5, np.inf])

    # if only the core is to be shown, restrict radial range
    if args.core_only and args.normalize:
        ranges = np.array([[0, 0.05], [3, 8.5]])  # units: R_vir, log K
    elif args.core_only and not args.normalize:
        ranges = np.array([[0, 100], [3, 8.5]])  # units: kpc, log K
    elif args.normalize:
        ranges = np.array([[0, 2], [3, 8.5]])  # units: R_vir, log K
    else:
        ranges = np.array([[0, 2000], [3, 8.5]])  # units: kpc, log K

    pipeline_config.update(
        {
            "what": args.what,
            "radial_bins": args.rbins,
            "temperature_bins": tbins,
            "log": args.log,
            "forbid_tree": args.forbid_tree,
            "ranges": ranges,
            "core_only": args.core_only,
            "normalize": args.normalize,
        }
    )
    if args.from_file:
        pipeline = IndividualProfilesFromFilePipeline(**pipeline_config)
    elif args.sim == "TNG-Cluster":
        pipeline = IndividualProfilesTNGClusterPipeline(**pipeline_config)
    else:
        pipeline = IndividualRadialProfilePipeline(**pipeline_config)
    sys.exit(pipeline.run())


if __name__ == "__main__":
    parser = scriptparse.BaseScriptParser(
        prog=f"python {Path(__file__).name}",
        description=(
            "Plot individual radial profiles of all halos in TNG with mass "
            "above 10^14 solar masses."
        ),
        allowed_sims=("TNG300", "TNG100", "TNG50", "TNG-Cluster"),
    )
    parser.add_argument(
        "-w",
        "--what",
        help=(
            "What type of radial profile to plot: temperature or density. Defaults "
            "to temperature."
        ),
        dest="what",
        type=str,
        default="temperature",
        choices=["temperature", "density"],
    )
    parser.add_argument(
        "-t",
        "--forbid-tree",
        help=(
            "Forbid the construction of a KDTree. Will cause the job to fail "
            "if construction of a KDTree is required in order to find the "
            "neighboring particles of all halos above the mass threshold."
        ),
        dest="forbid_tree",
        action="store_true",
    )
    parser.add_argument(
        "--log",
        help="Plot the figures in log scale instead of linear scale.",
        action="store_true",
        dest="log",
    )
    parser.add_argument(
        "-cc",
        "--cluster-core",
        help=(
            "Plot the core region of the cluster only. This will restrict the "
            "radial range of the plot to around 50 kpc physical size."
        ),
        dest="core_only",
        action="store_true",
    )
    parser.add_argument(
        "-a",
        "--absolute-distances",
        help=(
            "Instead of normalizing all halocentric distances to the virial "
            "radius of the cluster, use absolute distances in units of kpc."
        ),
        dest="normalize",
        action="store_false",
    )
    parser.add_argument(
        "-tb",
        "--tbins",
        help=(
            "The number of temperature bins, defaults to 50. Has no effect "
            "when plotting density profiles."
        ),
        dest="tbins",
        type=int,
        default=50,
        metavar="NUMBER",
    )
    parser.add_argument(
        "-rb",
        "--rbins",
        help="The number of radial bins, defaults to 50",
        dest="rbins",
        type=int,
        default=50,
        metavar="NUMBER",
    )

    # parse arguments
    try:
        args = parser.parse_args()
        main(args)
    except KeyboardInterrupt:
        print("Execution forcefully stopped.")
        sys.exit(1)
