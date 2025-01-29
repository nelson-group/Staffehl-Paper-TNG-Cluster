import argparse
import sys
from pathlib import Path

import yaml

root_dir = Path(__file__).parents[2].resolve()
sys.path.insert(0, str(root_dir / "src"))

from library import scriptparse
from pipelines.mass_trends.cool_gas_fracs_clusters import (
    ClusterCoolGasFromFilePipeline,
    ClusterCoolGasMassTrendPipeline,
)


def main(args: argparse.Namespace) -> None:
    """Create plot of gas mass trends for individual halos"""
    args.sim = "TNG-Cluster"
    # find type flag depending on field name
    if args.field is None:
        type_flag = "clusters_raw"
    else:
        type_flag = f"clusters_{args.field.lower().replace('-', '_')}"
    if args.gas_domain == "central":
        type_flag += "_core"
    elif args.gas_domain == "vr":
        type_flag += "_vr"

    # subdirectory for figures
    subdir = args.field.replace("-", "_")
    if args.gas_domain == "central":
        subdir += "/core"
    elif args.gas_domain == "vr":
        subdir += "/virial_radius"

    # base pipeline config dict
    pipeline_config = scriptparse.startup(
        args,
        "mass_trends",
        type_flag,
        figures_subdirectory=f"./../clusters/{subdir}",
        suppress_sim_name_in_files=True,
    )

    # add custom parameters
    pipeline_config.update(
        {
            "field": args.field.lower(),
            "color_scale": args.color_scale,
            "deviation_scale": args.deviation_scale,
            "gas_domain": args.gas_domain,
            "forbid_recalculation": args.forbid_recalculation,
            "force_recalculation": args.force_recalculation,
            "use_absolute_mass": args.absolute_mass,
        }
    )

    if args.from_file:
        pipeline = ClusterCoolGasFromFilePipeline(**pipeline_config)
    else:
        pipeline = ClusterCoolGasMassTrendPipeline(**pipeline_config)
    sys.exit(pipeline.run())


if __name__ == "__main__":
    # get list of available fields
    config_file = root_dir / "src/pipelines/mass_trends/plot_config.yaml"
    with open(config_file, "r") as f:
        stream = f.read()
    configuration = yaml.full_load(stream)
    available_fields = list(configuration.keys())

    # construct parser
    parser = scriptparse.BaseScriptParser(
        prog=f"python {Path(__file__).name}",
        description="Plot mass trends of gas of halos in TNG",
    )
    parser.remove_argument("sim")
    parser.remove_argument("processes")
    parser.add_argument(
        "--field",
        help=(
            "The field to use for the color data. Must be one of the "
            "supported fields."
        ),
        dest="field",
        choices=available_fields,
        required=True,
    )
    parser.add_argument(
        "--absolute-mass",
        help=(
            "Plot the absolute cool gas mass vs. cluster mass instead of the "
            "cool gas fraction. This will also use the absolute cool gas mass "
            "for all follow-up plots and statistics."
        ),
        dest="absolute_mass",
        action="store_true",
    )
    parser.add_argument(
        "--color-scale",
        help=(
            "The normalisation for the color data. If not explicitly set, "
            "the default set in the config is used."
        ),
        dest="color_scale",
        choices=["log", "linear"],
        default=None,
    )
    parser.add_argument(
        "--deviation-scale",
        help=(
            "The scale of the deviation plot, which can either be log or "
            "linear. The colorbar will extent to both sides of unity (or zero "
            "for logarithmic scaling) in the corresponding scale. If not set, "
            "the default set in the config is used."
        ),
        dest="deviation_scale",
        choices=["log", "linear"],
        default=None,
    )
    parser.add_argument(
        "--gas-domain",
        help=(
            "The domain for the gas fraction. The y-axis can either show the "
            "gas fraction of the entire cluster out to 2 virial radii (halo), "
            "only the in the core region, meaning within 5%% of the virial "
            "radius (central), or within only the virial radius (vr)."
        ),
        dest="gas_domain",
        choices=["halo", "central", "vr"],
        default="halo",
    )
    exclusive_group = parser.add_mutually_exclusive_group(required=False)
    exclusive_group.add_argument(
        "-xr",
        "--forbid-recalculation",
        help=(
            "Forbid the recalculation of gas fractions and the color data. If "
            "color data is not available on file, the pipeline will fail. If "
            "the base data (cool gas fractions/masses) are not available on "
            "file, they will be loaded from the radial profile data files."
        ),
        dest="forbid_recalculation",
        action="store_true",
    )
    exclusive_group.add_argument(
        "-fr",
        "--force-recalculation",
        help=(
            "Force the recalculation of the color data. Gas fraction will "
            "be read from file if available, but if it is not found will be "
            "recalculated as well. If the gas domain is set to the virial "
            "radius, missing gas fraction data or radial density profile data "
            "will lead to an exception, as recalculating gas fraction and "
            "mass from simulation data directly is not currently implemented."
        ),
        dest="force_recalculation",
        action="store_true",
    )

    # parse arguments
    try:
        args = parser.parse_args()
        main(args)
    except KeyboardInterrupt:
        print("Execution forcefully stopped.")
        sys.exit(1)
