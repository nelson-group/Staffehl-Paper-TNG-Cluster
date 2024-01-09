"""
Pipeline to plot radial temperature profiles for individual halos.
"""
from __future__ import annotations

import logging
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import illustris_python as il
import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial import KDTree

from library import compute
from library.data_acquisition import gas_daq, halos_daq
from library.loading import load_radial_profiles
from library.plotting import plot_radial_profiles
from library.processing import selection, statistics
from pipelines.base import DiagnosticsPipeline

if TYPE_CHECKING:
    from numpy.typing import NDArray


@dataclass
class IndividualTemperatureProfilePipeline(DiagnosticsPipeline):
    """
    Pipeline to create plots of radial temperature distribution.

    Pipeline creates 2D histograms of the temperature distribution with
    radial distance to the center of the halo, including particles not
    bound to the halo. It does this for every halo above 10^14 solar
    masses in virial mass.

    This pipeline must load all particle data in order to be able to
    plot gas particles that do ot belong to halos as well.
    """

    radial_bins: int
    temperature_bins: int
    log: bool
    forbid_tree: bool = True  # whether KDTree construction is allowed

    ranges: ClassVar[NDArray] = np.array([[0, 2], [3, 8.5]])  # hist ranges

    def run(self) -> int:
        """
        Create radial temperature profiles for all halos above 10^14 M.

        Steps:

        1. Load halo data.
        2. Restrict halo data to halos above mass threshold.
        3. Load gas cell data required for temperature calculation.
        4. Calculate gas cell temperature, discard obsolete data.
        5. Load gas cell position data.
        6. For every selected halo:
           i. Query gas cells for neighbors (either using KDTree or pre-
              saved particle IDs)
           ii. Create a 2D histogram of temperature vs. distance.
           iii. Save figure and data to file.
           iv. Discard data in memory.

        :return: Exit code.
        """
        # Step 0: create directories, start memory monitoring, timing
        self._create_directories(
            subdirs=["particle_ids", "temperature_profiles"], force=True
        )
        tracemalloc.start()
        begin = time.time()

        # Step 1: acquire halo data
        fields = [self.config.mass_field, self.config.radius_field, "GroupPos"]
        halo_data = halos_daq.get_halo_properties(
            self.config.base_path, self.config.snap_num, fields=fields
        )
        mem = tracemalloc.get_traced_memory()
        self._memlog("Halo gas data memory usage", mem[0], "MB")

        # Step 2: select only halos above threshold mass
        logging.info("Restricting halo data to log(M) > 14.")
        mask = np.digitize(halo_data[self.config.mass_field], [0, 1e14, 1e25])
        selected_halos = {
            "ids":
                selection.mask_quantity(
                    halo_data["IDs"], mask, index=2, compress=True
                ),
            "masses":
                selection.mask_quantity(
                    halo_data[self.config.mass_field],
                    mask,
                    index=2,
                    compress=True
                ),
            "positions":
                selection.mask_quantity(
                    halo_data["GroupPos"], mask, index=2, compress=True
                ),
            "radii":
                selection.mask_quantity(
                    halo_data[self.config.radius_field],
                    mask,
                    index=2,
                    compress=True
                ),
        }
        del halo_data, mask  # free memory
        mem = tracemalloc.get_traced_memory()
        self._memlog("Memory usage after restricting halos", mem[0], "kB")
        timepoint = self._timeit(begin, "loading and selecting halo data")

        # Step 3: Load gas cell data for temperature
        logging.info("Loading gas cell data for all gas particles.")
        fields = ["InternalEnergy", "ElectronAbundance", "StarFormationRate"]
        gas_data = il.snapshot.loadSubset(
            self.config.base_path,
            self.config.snap_num,
            partType=0,
            fields=fields
        )
        mem = tracemalloc.get_traced_memory()
        self._memlog("Memory used after loading particles", mem[0])
        timepoint = self._timeit(timepoint, "loading gas cell data")

        # Step 4: Calculate temperature of every gas cell
        part_shape = gas_data["InternalEnergy"].shape
        logging.info(
            f"Calculating temperature for {part_shape[0]:,} gas cells."
        )
        temps = compute.get_temperature(
            gas_data["InternalEnergy"],
            gas_data["ElectronAbundance"],
            gas_data["StarFormationRate"],
        )
        # clean up unneeded data
        del gas_data
        # diagnostics
        timepoint = self._diagnostics(
            timepoint, "calculating gas temperatures"
        )

        # Step 5: Load gas cell position data
        gas_data = gas_daq.get_gas_properties(
            self.config.base_path,
            self.config.snap_num,
            fields=["Coordinates", "Masses"],
        )
        gas_data["Temperatures"] = temps
        # diagnostics
        timepoint = self._diagnostics(timepoint, "loading gas cell positions")

        # Step 6: check if KDTree construction is required
        part_id_directory = Path(self.paths["data_dir"]) / "particle_ids"
        available_ids = set([f.stem for f in part_id_directory.iterdir()])
        required_ids = set(
            [f"particles_halo_{i}" for i in selected_halos["ids"]]
        )
        # check whether all halos have particle ID files available
        if required_ids.issubset(available_ids):
            logging.info(
                "Found particle IDs of associated particles for all halos. "
                "Continuing with existing particle ID data."
            )
            use_tree = False
        else:
            # if the user explicitly forbade tree creation, cancel execution
            if self.forbid_tree:
                logging.fatal(
                    "Not all selected halos have associated particle IDs on "
                    "file, but tree creation was forbidden. Cannot continue "
                    "with the job at hand, canceling execution."
                )
                return 2
            # otherwise, create the tree
            logging.info(
                "Not all selected halos have particle IDs of associated "
                "particles saved. Continuing with KDTree construction."
            )
            logging.info("Constructing KDTree from particle positions.")
            use_tree = True
            positions_tree = KDTree(
                gas_data["Coordinates"],
                balanced_tree=True,
                compact_nodes=True,
            )
            # diagnostics
            timepoint = self._diagnostics(timepoint, "constructing KDTree")
        # prepare variables for querying
        workers = self.processes if self.processes else 1

        # Step 7: Create the radial profiles
        logging.info("Begin processing halos.")
        for i in range(len(selected_halos["ids"])):
            halo_id = selected_halos["ids"][i]
            # find all particles within 2 * R_vir
            if use_tree:
                neighbors = positions_tree.query_ball_point(
                    selected_halos["positions"][i],
                    2 * selected_halos["radii"][i],
                    workers=workers
                )
            else:
                neighbors = np.load(
                    part_id_directory / f"particles_halo_{halo_id}.npy"
                )
            # slice and normalize distances
            part_positions = gas_data["Coordinates"][neighbors]
            part_distances = np.linalg.norm(
                part_positions - selected_halos["positions"][i], axis=1
            ) / selected_halos["radii"][i]
            # save data to file
            if self.to_file and use_tree:
                logging.debug(
                    f"Saving particle indices and distances of halo {halo_id} "
                    "to file."
                )
                filepath = self.paths["data_dir"] / "particle_ids"
                np.save(filepath / f"particles_halo_{halo_id}.npy", neighbors)
            # slice temperatures
            part_temperatures = gas_data["Temperatures"][neighbors]
            # weight by gas mass
            weights = gas_data["Masses"][neighbors]
            weights /= np.sum(gas_data["Masses"][neighbors])
            # create histogram
            h, _, _, = np.histogram2d(
                part_distances,
                np.log10(part_temperatures),
                range=self.ranges,
                bins=(self.radial_bins, self.temperature_bins),
                weights=weights,
            )
            hn, xe, ye = statistics.column_normalized_hist2d(
                part_distances,
                np.log10(part_temperatures),
                ranges=self.ranges,
                bins=(self.radial_bins, self.temperature_bins),
                values=weights,
                normalization="density",
            )
            # save data
            if self.to_file:
                logging.debug(
                    f"Writing histogram data for halo {halo_id} to file."
                )
                filepath = Path(
                    self.paths["data_dir"]
                ) / "temperature_profiles"
                filename = (
                    f"{self.paths['data_file_stem']}_halo_{halo_id}.npz"
                )
                np.savez(
                    filepath / filename,
                    histogram=hn,
                    original_histogram=h,
                    xedges=xe,
                    yedges=ye,
                    halo_id=halo_id,
                    halo_mass=selected_halos["masses"][i],
                )
            # plot and save data
            self._plot_halo(
                halo_id=halo_id,
                halo_mass=selected_halos["masses"][i],
                histogram=hn,
                xedges=xe,
                yedges=ye,
            )
            # cleanup
            del part_positions, part_distances, part_temperatures, weights
            del hn, h, xe, ye

        timepoint = self._diagnostics(
            timepoint, "plotting individual profiles"
        )

        self._timeit(begin, "total execution")
        tracemalloc.stop()
        return 0

    def _plot_halo(
        self,
        halo_id: int,
        halo_mass: float,
        histogram: NDArray,
        xedges: NDArray,
        yedges: NDArray,
    ) -> None:
        """
        Plot the histogram of a single halo.

        :param halo_id: The halo ID.
        :param halo_mass: The mass of the halo in units of solar masses.
        :param histogram: The (N, N) shape array of the histogram data.
        :param xedges: The edges of the x bins.
        :param yedges: The edges of the y bins.
        """
        fig, axes = plt.subplots(figsize=(5, 4))
        fig.set_tight_layout(True)
        title = (
            f"Temperature profile of halo {halo_id} "
            rf"($10^{{{np.log10(halo_mass):.2f}}} M_\odot$)"
        )
        ranges = [xedges[0], xedges[-1], yedges[0], yedges[-1]]
        if self.log:
            plot_radial_profiles.plot_2d_radial_profile(
                fig,
                axes,
                histogram,
                ranges,
                title=title,
                cbar_label="Normalized gas mass fraction (log10)",
                scale="log",
                cbar_ticks=[0, -1, -2, -3, -4, -5],
            )
        else:
            plot_radial_profiles.plot_2d_radial_profile(
                fig,
                axes,
                histogram,
                ranges,
                title=title,
                cbar_label="Normalized gas mass fraction"
            )

        # save figure
        if self.no_plots:
            return
        name = f"{self.paths['figures_file_stem']}_halo_{halo_id}.pdf"
        path = Path(self.paths["figures_dir"]) / f"halo_{halo_id}"
        if not path.exists():
            logging.debug(
                f"Creating missing figures directory for halo "
                f"{halo_id}."
            )
            path.mkdir(parents=True)
        fig.savefig(path / name, bbox_inches="tight")
        plt.close(fig)


class ITProfilesFromFilePipeline(IndividualTemperatureProfilePipeline):
    """
    Pipeline to recreate the temp profiles of individual halos from file.
    """

    def __post_init__(self) -> None:
        return super().__post_init__()

    def run(self) -> int:
        """
        Recreate radial temperature profiles from file.

        Steps for every halo:

        1. Load data from file
        2. Plot the halo data

        :return: Exit code.
        """
        # Step 0: verify directories
        if exit_code := self._verify_directories() > 0:
            return exit_code

        if self.no_plots:
            logging.warning(
                "Was asked to load data but not plot it. This is pretty "
                "pointless and probably not what you wanted."
            )
            return 1

        # Step 1: load data
        load_generator = load_radial_profiles.load_individuals_2d_profile(
            self.paths["data_dir"] / "temperature_profiles",
            (self.radial_bins, self.temperature_bins)
        )
        for halo_data in load_generator:
            self._plot_halo(**halo_data)
