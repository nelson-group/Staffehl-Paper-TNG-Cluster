import argparse
import logging
import logging.config
import sys
import time
from pathlib import Path

# import the helper scripts
cur_dir = Path(__file__).parent.resolve()
sys.path.append(str(cur_dir.parent.parent / "src"))
import logging_config
from processors import temperature_gallery


def main(args: argparse.Namespace) -> None:
    """Create a gallery of temperature distributions"""
    logging_cfg = logging_config.get_logging_config("INFO")
    logging.config.dictConfig(logging_cfg)
    logger = logging.getLogger("root")

    # sim data
    if args.sim == "TEST_SIM":
        SIMULATION = "TNG50-4"
    elif args.sim == "DEV_SIM":
        SIMULATION = "TNG50-3"
    elif args.sim == "MAIN_SIM":
        SIMULATION = "TNG300-1"
    else:
        raise ValueError(f"Unknown simulation type {args.sim}.")

    # plotter for hist data
    MASS_BINS = [1e8, 1e9, 1e10, 1e11, 1e12, 1e13, 1e14, 1e15]
    hist_plotter = temperature_gallery.TemperatureDistributionGalleryProcessor(
        SIMULATION, logger, args.bins, MASS_BINS
    )

    if args.load:
        sim = SIMULATION.replace("-", "_")
        file_name = f"temperature_gallery_{sim}.npz"
        file_path = hist_plotter.config.data_home / "001" / file_name
        hist_plotter.load_data(file_path)
    else:
        begin = time.time()
        hist_plotter.get_data(
            0, args.quiet, post_kwargs={"to_file": args.to_file}
        )
        end = time.time()

        # get time spent on computation
        time_diff = end - begin
        time_fmt = time.strftime('%H:%M:%S', time.gmtime(time_diff))
        logger.info(f"Spent {time_fmt} hours on execution.")

    if args.no_plots:
        sys.exit(0)

    # plot histograms
    for i in range(len(MASS_BINS) - 1):
        hist_plotter.plot_data(i)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog=f"python {Path(__file__).name}",
        description="Plot temperature distribution galleries of halos in TNG",
    )
    parser.add_argument(
        "-s",
        "--sim",
        help=(
            "Type of the simulation to use; main sim is TNG300-1, dev sim "
            "is TNG50-3 and test sim is TNG50-4"
        ),
        dest="sim",
        type=str,
        default="MAIN_SIM",
        choices=["MAIN_SIM", "DEV_SIM", "TEST_SIM"],
    )
    parser.add_argument(
        "-f",
        "--to-file",
        help=(
            "Whether to write the histogram and virial temperature data "
            "calclated to file"
        ),
        dest="to_file",
        action="store_true",
    )
    parser.add_argument(
        "-n",
        "--no-plots",
        help=(
            "Suppresses creation of plots, use to prevent overwriting "
            "existing files."
        ),
        dest="no_plots",
        action="store_true",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        help=(
            "Prevent progress information to be emitted. Has no effect when "
            "multiprocessing is used."
        ),
        dest="quiet",
        action="store_true",
    )
    parser.add_argument(
        "-l",
        "--load-data",
        help=("Load data from file instead of selecting new halos."),
        dest="load",
        action="store_true",
    )
    parser.add_argument(
        "-b",
        "--bins",
        help="The number of temperature bins, defaults to 50",
        dest="bins",
        type=int,
        default=50,
    )

    # parse arguments
    try:
        args = parser.parse_args()
        main(args)
    except KeyboardInterrupt:
        print(
            "Execution forcefully stopped. Some subprocesses might still be "
            "running and need to be killed manually if multiprocessing was "
            "used."
        )
        sys.exit(1)
