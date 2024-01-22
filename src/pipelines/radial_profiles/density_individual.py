"""
Pipeline to plot radial density profiles for individual halos.
"""
from __future__ import annotations

import logging
import time
import tracemalloc
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial import KDTree

from library.data_acquisition import gas_daq, halos_daq
from library.loading import load_radial_profiles
from library.plotting import plot_radial_profiles
from library.processing import selection, statistics
from pipelines.base import DiagnosticsPipeline

if TYPE_CHECKING:
    from numpy.typing import NDArray

warnings.warn(
    "This module is old and has been replaced by the newer module "
    "'individuals' which includes density plots for cool, warm, and hot "
    "gas. Use this instead.",
    DeprecationWarning,
    stacklevel=2,
)


@dataclass
class IndividualDensityProfilePipelineLegacy(DiagnosticsPipeline):
    """
    Pipeline to create plots of radial density distribution.

    Pipeline creates histograms of the density distribution with
    radial distance to the center of the halo, including particles not
    bound to the halo. It does this for every halo above 10^14 solar
    masses in virial mass.

    This pipeline must load all particle data in order to be able to
    plot gas particles that do ot belong to halos as well.
    """

    radial_bins: int
    log: bool
    forbid_tree: bool = True  # whether KDTree construction is allowed

    def __post_init__(self):
        return super().__post_init__()

    def run(self) -> int:
        """
        Create radial density profiles for all halos above 10^14 M.

        Steps:

        1. Load halo data.
        2. Restrict halo data to halos above mass threshold.
        3. Load gas cell data required for temperature calculation.
        4. Calculate gas cell temperatures, discard obsolete data.
        5. Load gas cell position and mass data.
        6. For every selected halo:
           1. Query gas cells for neighbors (either using KDTree or pre-
              saved particle IDs)
           2. Bin gas by temperature.
           3. Create a histogram of mass vs. distance for total and
              for binned gas.
           4. Normalize every bin by the shell volume to get density.
           5. Save figure and data to file.
           6. Discard data in memory.

        :return: Exit code.
        """
        # Step 0: create directories, start memory monitoring, timing
        self._create_directories(
            subdirs=["particle_ids", "density_profiles"], force=True
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

        # Step 2: select only halos above threshhold mass
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

        # Step 3: Load gas cell position and mass data
        gas_data = gas_daq.get_gas_properties(
            self.config.base_path,
            self.config.snap_num,
            fields=["Coordinates", "Masses"],
        )
        # diagnostics
        timepoint = self._diagnostics(
            timepoint, "loading gas cell positions and mass"
        )

        # Step 4: check if KDTree construction is required
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

        # Step 5: Create the radial profiles
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
                    f"Saving particle indices of halo {halo_id} to file."
                )
                filepath = self.paths["data_dir"] / "particle_ids"
                np.save(filepath / f"particles_halo_{halo_id}.npy", neighbors)
            # weight by gas mass
            weights = gas_data["Masses"][neighbors]
            # create histogram
            hist, edges = statistics.volume_normalized_radial_profile(
                part_distances,
                weights,
                self.radial_bins,
                selected_halos["radii"][i],
            )
            # save data
            if self.to_file:
                logging.debug(
                    f"Writing histogram data for halo {halo_id} to file."
                )
                filepath = Path(self.paths["data_dir"]) / "density_profiles"
                filename = (
                    f"{self.paths['data_file_stem']}_halo_{halo_id}.npz"
                )
                np.savez(
                    filepath / filename,
                    histogram=hist,
                    edges=edges,
                    halo_id=halo_id,
                    halo_mass=selected_halos["masses"][i],
                )
            # plot and save data
            self._plot_halo(
                halo_id=halo_id,
                halo_mass=selected_halos["masses"][i],
                histogram=hist,
                edges=edges,
            )
            # cleanup
            del part_positions, part_distances, weights, hist, edges

        self._diagnostics(timepoint, "plotting individual profiles")

        self._timeit(begin, "total execution")
        tracemalloc.stop()
        return 0

    def _plot_halo(
        self,
        halo_id: int,
        halo_mass: float,
        histogram: NDArray,
        edges: NDArray,
    ) -> None:
        """
        Plot the histogram of a single halo.

        :param halo_id: The halo ID.
        :param halo_mass: The mass of the halo in units of solar masses.
        :param histogram: The (N,) shape array of the histogram data.
        :param edges: The edges of the radial bins.
        """
        fig, axes = plt.subplots(figsize=(5, 4))
        fig.set_tight_layout(True)
        title = (
            rf"Halo {halo_id} ($10^{{{np.log10(halo_mass):.2f}}} M_\odot$)"
        )
        plot_radial_profiles.plot_1d_radial_profile(
            axes, histogram, edges, log=self.log, title=title
        )

        # save figure
        if self.no_plots:
            return
        if self.config.sim_name == "TNG-Cluster":
            htype = "cluster"
        else:
            htype = "halo"
        name = f"{self.paths['figures_file_stem']}_{htype}_{halo_id}.pdf"
        path = Path(self.paths["figures_dir"]) / f"{htype}_{halo_id}"
        if not path.exists():
            logging.debug(
                f"Creating missing figures directory for halo "
                f"{halo_id}."
            )
            path.mkdir(parents=True)
        fig.savefig(path / name, bbox_inches="tight")
        plt.close(fig)


class IDProfilesFromFilePipeline(IndividualDensityProfilePipelineLegacy):
    """
    Pipeline to recreate the temp profiles of individual halos from file.
    """

    def __post_init__(self) -> None:
        return super().__post_init__()

    def run(self) -> int:
        """
        Recreate radial density profiles from file.

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
        load_generator = load_radial_profiles.load_individuals_1d_profile(
            self.paths["data_dir"], self.radial_bins
        )
        for halo_data in load_generator:
            self._plot_temperature_profile(**halo_data)


class IndivDensityTNGClusterPipeline(IndividualDensityProfilePipelineLegacy):
    """
    Pipeline to create radial density profiles for TNG cluster halos.

    Pipeline creates histograms of the density distribution with
    radial distance to the center of the halo, including particles not
    bound to the halo. It does this for every one of the 352 original
    zoom-in clusters of TNG Cluster.

    This pipeline is significantly less memory-intensive than the one
    for the normal TNG simulations as it does not require loading all
    particle data at once.
    """

    def run(self) -> int:
        """
        Create radial density profiles for zoom-in clusters of TNG Cluster.

        Steps:

        1. Load halo data.
        2. For every halo:
           1. Load gas cell data
           2. Create histogram of density vs. distance
           3. Save data and figures to file
           4. Cleanup

        :return: Exir code.
        """
        # Step 0: create directories, start monitoring
        self._create_directories(subdirs=["density_profiles"], force=True)
        tracemalloc.start()
        begin = time.time()

        # Step 1: Load halo data
        fields = [self.config.mass_field, self.config.radius_field, "GroupPos"]
        halo_data = halos_daq.get_halo_properties(
            self.config.base_path,
            self.config.snap_num,
            fields=fields,
            cluster_restrict=True,
        )
        timepoint = self._diagnostics(begin, "loading halo data", unit="kB")

        # Step 2: Loop over halos
        logging.info("Start processing individual halos.")
        for i, halo_id in enumerate(halo_data["IDs"]):
            if not self.quiet:
                logging.info(f"Processing halo {halo_id} ({i}/352).")
            # Step 2.1: Load gas cell data
            gas_data = gas_daq.get_gas_properties(
                self.config.base_path,
                self.config.snap_num,
                fields=["Coordinates", "Masses"],
                cluster=halo_id,
            )

            # Step 2.2: Create histograms
            gas_distances = np.linalg.norm(
                gas_data["Coordinates"] - halo_data["GroupPos"][i], axis=1
            ) / halo_data[self.config.radius_field][i]
            hist, edges = statistics.volume_normalized_radial_profile(
                gas_distances,
                gas_data["Masses"],
                self.radial_bins,
                halo_data[self.config.radius_field][i],
                np.array([0., 2.]),
            )

            # Step 2.3: Save data and figures to file
            if self.to_file:
                logging.debug(f"Writing data for cluster {halo_id} to file.")
                filepath = Path(self.paths["data_dir"]) / "density_profiles"
                filename = (
                    f"{self.paths['data_file_stem']}_cluster_{halo_id}.npz"
                )
                np.savez(
                    filepath / filename,
                    histogram=hist,
                    edges=edges,
                    halo_id=halo_id,
                    halo_mass=halo_data[self.config.mass_field][i],
                )
            self._plot_temperature_profile(
                halo_id=halo_id,
                halo_mass=halo_data[self.config.mass_field][i],
                histogram=hist,
                edges=edges,
            )

            # Step 2.4: cleanup and diagnostics
            del gas_data, gas_distances, hist, edges
            timepoint = self._diagnostics(
                timepoint, f"procesing halo {halo_id} ({i}/352)"
            )

        self._diagnostics(begin, "total execution")
        tracemalloc.stop()
        return 0
