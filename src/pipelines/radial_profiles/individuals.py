"""
Pipeline to plot radial temperature profiles for individual halos.
"""
from __future__ import annotations

import logging
import sys
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

import illustris_python as il
import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial import KDTree

from library import compute
from library.data_acquisition import gas_daq, halos_daq
from library.loading import load_radial_profiles
from library.plotting import common, plot_radial_profiles
from library.processing import selection, statistics
from pipelines.base import DiagnosticsPipeline

if TYPE_CHECKING:
    from numpy.typing import NDArray


@dataclass
class IndividualRadialProfilePipeline(DiagnosticsPipeline):
    """
    Pipeline to create plots of radial temperature/density distribution.

    Pipeline creates histograms of the distribution of temperature or
    density with radial distance to the center of the halo, including
    particles not bound to the halo. It does this for every halo above
    10^14 solar masses in virial mass.

    This pipeline must load all particle data in order to be able to
    plot gas particles that do ot belong to halos as well.
    """

    what: Literal["temperature", "density"]
    radial_bins: int
    temperature_bins: int
    log: bool
    forbid_tree: bool = True  # whether KDTree construction is allowed
    ranges: NDArray = np.array([[0, 2], [3, 8.5]])  # hist ranges
    core_only: bool = False

    divisions: ClassVar[NDArray] = np.array([4.5, 5.5])  # in log K

    def __post_init__(self):
        self.use_tree = not self.forbid_tree
        if self.config.sim_name == "TNG-Cluster":
            self.group_name = "cluster"
        else:
            self.group_name = "halo"
        # particle id directory
        if self.core_only:
            pid_dir = Path(self.paths["data_dir"]) / "particle_ids_core"
            self.suffix = "_core"
        else:
            pid_dir = Path(self.paths["data_dir"]) / "particle_ids"
            self.suffix = ""
        self.part_id_dir = pid_dir

    def run(self) -> int:
        """
        Create radial profiles for all halos above 10^14 M.

        Can either be a radial temperature profile or a radial density
        profile, depending on choice of ``self.what``.

        Steps:

        1. Load halo data.
        2. Restrict halo data to halos above mass threshold.
        3. Calculate virial temperature for selected halos
        4. Load gas cell data required for temperature calculation.
        5. Calculate gas cell temperature, discard obsolete data.
        6. Load gas cell position and mass data.
        7. For every selected halo:
           i. Query gas cells for neighbors (either using KDTree or pre-
              saved particle IDs)
           ii. Create a 2D histogram of temperature vs. distance.
           iii. Save figure and data to file.
           iv. Discard data in memory.

        :return: Exit code.
        """
        # Step 0: create directories, start memory monitoring, timing
        self._create_directories(
            subdirs=[
                "particle_ids",
                "particle_ids_core",
                f"temperature_profiles{self.suffix}",
                f"density_profiles{self.suffix}",
            ],
            force=True
        )
        if self.core_only:
            logging.info("Received instructions to only plot halo cores.")
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

        # Step 3: calculate virial temperature for halos
        logging.info("Calculating virial temperature for selected halos.")
        selected_halos["virial_temperatures"] = compute.get_virial_temperature(
            selected_halos["masses"], selected_halos["radii"]
        )
        mem = tracemalloc.get_traced_memory()
        self._memlog(
            "Memory used after calculating virial temperatures", mem[0], "kB"
        )
        timepoint = self._timeit(timepoint, "calculating virial temperatures")

        # Step 4: Load gas cell data for temperature
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

        # Step 5: Calculate temperature of every gas cell
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

        # Step 6: Load gas cell position and mass data
        gas_data = gas_daq.get_gas_properties(
            self.config.base_path,
            self.config.snap_num,
            fields=["Coordinates", "Masses"],
        )
        gas_data["Temperatures"] = temps
        # diagnostics
        timepoint = self._diagnostics(timepoint, "loading gas cell positions")

        # Step 7: check if KDTree construction is required
        workers, positions_tree = self._check_if_tree_required(selected_halos, gas_data)
        if self.use_tree:
            timepoint = self._diagnostics(timepoint, "constructing KDTree")

        # Step 8: Create the radial profiles
        logging.info("Begin processing halos.")
        if self.what == "temperature":
            worker_method = self._process_halo_temperature_profile
        elif self.what == "density":
            worker_method = self._process_halo_density_profile
        else:
            logging.fatal(f"Unrecognized plot type {self.what}.")
            return 3
        for i in range(len(selected_halos["ids"])):
            kwargs = {
                "halo_id": selected_halos["ids"][i],
                "halo_position": selected_halos["positions"][i],
                "halo_mass": selected_halos["masses"][i],
                "virial_radius": selected_halos["radii"][i],
                "virial_temperature": selected_halos["virial_temperatures"][i],
                "gas_data": gas_data,
                "positions_tree": positions_tree,
            }
            if self.what == "density":
                kwargs.pop("virial_temperature")  # not required
            worker_method(**kwargs)

        self._diagnostics(timepoint, "plotting individual profiles")

        self._timeit(begin, "total execution")
        tracemalloc.stop()
        return 0

    def _check_if_tree_required(
        self, selected_halos: dict[str, NDArray], gas_data: dict[str, NDArray]
    ) -> tuple[int, KDTree | None]:
        """
        Checks whether the construction of a KDTree is required.

        If the construction of a KDTree is required, the tree will be
        constructed and ``self.use_tree`` is set to True. Otherwise, or
        if three construction is explicitly forbidden, ``self.use_tree``
        will be set to False.

        Returns the number of workers and the KDTree object, if needed,
        otherwise returns the tuple (1, None) as a dummy return value.

        :param selected_halos: The dictionary containing the restricted
            halo data.
        :param gas_data: The dictionary containing the gas cell data.
        :return: The tuple of the number of workers and the KDTree, if
            construction of it is required.
        """
        try:
            available_ids = set([f.stem for f in self.part_id_dir.iterdir()])
        except IOError:
            logging.warning(
                f"Could not find or read the particle IDs from the directory "
                f"{self.part_id_dir}. Did you delete or move the directory?"
            )
            available_ids = set()
        required_ids = set(
            [
                f"particles_halo_{i}{self.suffix}"
                for i in selected_halos["ids"]
            ]
        )

        # check whether all halos have particle ID files available
        if required_ids.issubset(available_ids):
            logging.info(
                "Found particle IDs of associated particles for all halos. "
                "Continuing with existing particle ID data."
            )
            self.use_tree = False
            positions_tree = None
        else:
            # if the user explicitly forbade tree creation, cancel execution
            if self.forbid_tree:
                logging.fatal(
                    "Not all selected halos have associated particle IDs on "
                    "file, but tree creation was forbidden. Cannot continue "
                    "with the job at hand, canceling execution."
                )
                sys.exit(2)
            # otherwise, create the tree
            logging.info(
                "Not all selected halos have particle IDs of associated "
                "particles saved. Continuing with KDTree construction."
            )
            logging.info("Constructing KDTree from particle positions.")
            self.use_tree = True
            positions_tree = KDTree(
                gas_data["Coordinates"],
                balanced_tree=True,
                compact_nodes=True,
            )

        # prepare variables for querying
        workers = self.processes if self.processes else 1
        return workers, positions_tree

    def _process_halo_temperature_profile(
        self,
        halo_id: int,
        halo_position: NDArray,
        halo_mass: float,
        virial_radius: float,
        virial_temperature: float,
        gas_data: dict[str, NDArray],
        positions_tree: KDTree | None,
    ) -> None:
        """
        Process a single halo into a temperature radial profile.

        :param halo_id: The ID of the halo.
        :param halo_position: The 3D vector pointing to the position
            of the halo, in units of kpc.
        :param halo_mass: The mass of the halo in units of solar masses.
        :param virial_radius: The virial radius of the halo, in units
            of kpc.
        :param virial_temperature: The virial temperature of the halo in
            units of Kelvin.
        :param gas_data: The dictionary of the gas cell data.
        :param positions_tree: If ``self.use_tree`` is True, meaning
            that neighboring particles must be queried using a KDTree,
            this must be the KDTree of all particle positions in the
            simulation. Otherwise, it can be set to None.
        :return: None
        """
        restricted_gas_data = self._restrict_gas_data_to_halo(
            gas_data, halo_id, halo_position, virial_radius, positions_tree
        )

        # weight by gas mass
        weights = restricted_gas_data["Masses"]
        weights /= np.sum(restricted_gas_data["Masses"])

        # create histogram
        h, _, _, = np.histogram2d(
            restricted_gas_data["Distances"],
            np.log10(restricted_gas_data["Temperatures"]),
            range=self.ranges,
            bins=(self.radial_bins, self.temperature_bins),
            weights=weights,
        )
        hn, xe, ye = statistics.column_normalized_hist2d(
            restricted_gas_data["Distances"],
            np.log10(restricted_gas_data["Temperatures"]),
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
            filepath = (
                Path(self.paths["data_dir"])
                / f"temperature_profiles{self.suffix}"
            )
            filename = (
                f"{self.paths['data_file_stem']}_{self.group_name}_"
                f"{halo_id}.npz"
            )
            np.savez(
                filepath / filename,
                histogram=hn,
                original_histogram=h,
                xedges=xe,
                yedges=ye,
                halo_id=halo_id,
                halo_mass=halo_mass,
                virial_temperature=virial_temperature,
            )  # yapf: disable

        # plot and save data
        self._plot_temperature_profile(
            halo_id=halo_id,
            halo_mass=halo_mass,
            virial_temperature=virial_temperature,
            histogram=hn,
            xedges=xe,
            yedges=ye,
        )

        # cleanup
        del restricted_gas_data, weights
        del hn, h, xe, ye

    def _process_halo_density_profile(
        self,
        halo_id: int,
        halo_position: NDArray,
        halo_mass: float,
        virial_radius: float,
        gas_data: dict[str, NDArray],
        positions_tree: KDTree | None,
    ) -> None:
        """
        Process a single halo into a density radial profile.

        Steps:

        1. Query gas cells for neighbors (either using KDTree or pre-
           saved particle IDs)
        2. Bin gas by temperature.
        3. Create a histogram of mass vs. distance for total and for
           binned gas.
        4. Normalize every bin by the shell volume to get density.
        5. Save figure and data to file.
        6. Discard data in memory.

        :param halo_id: The ID of the halo.
        :param halo_position: The 3D vector pointing to the position
            of the halo, in units of kpc.
        :param halo_mass: The mass of the halo in units of solar masses.
        :param virial_radius: The viriral radius of the halo, in units
            of kpc.
        :param gas_data: The dictionary of the gas cell data.
        :param positions_tree: If ``self.use_tree`` is True, meaning
            that neighboring particles must be queried using a KDTree,
            this must be the KDTree of all particle positions in the
            simulation. Otherwise, it can be set to None.
        :return: None
        """
        restricted_gas_data = self._restrict_gas_data_to_halo(
            gas_data, halo_id, halo_position, virial_radius, positions_tree
        )

        # bin gas particles by temperature:
        mask = np.digitize(
            np.log10(restricted_gas_data["Temperatures"]),
            self.temperature_bins,
        )

        # create a total density profile
        total, edges = statistics.volume_normalized_radial_profile(
            restricted_gas_data["Distances"],
            restricted_gas_data["Masses"],
            self.radial_bins,
            virial_radius,
            radial_range=self.ranges[0],
        )

        # create density profile for cool gas
        masses = restricted_gas_data["Masses"]
        cool, _ = statistics.volume_normalized_radial_profile(
            selection.mask_quantity(restricted_gas_data["Distances"], mask, index=1),
            selection.mask_quantity(masses, mask, index=1),
            self.radial_bins,
            virial_radius,
            radial_range=self.ranges[0],
        )
        warm, _ = statistics.volume_normalized_radial_profile(
            selection.mask_quantity(restricted_gas_data["Distances"], mask, index=2),
            selection.mask_quantity(masses, mask, index=2),
            self.radial_bins,
            virial_radius,
            radial_range=self.ranges[0],
        )
        hot, _ = statistics.volume_normalized_radial_profile(
            selection.mask_quantity(restricted_gas_data["Distances"], mask, index=3),
            selection.mask_quantity(masses, mask, index=3),
            self.radial_bins,
            virial_radius,
            radial_range=self.ranges[0],
        )
        # np.testing.assert_allclose(total, hot + cool + warm, rtol=0.03)

        # write data to file
        if self.to_file:
            logging.debug(f"Writing data for halo {halo_id} to file.")
            filepath = (
                Path(self.paths["data_dir"]) / f"density_profiles{self.suffix}"
            )
            filename = (
                f"{self.paths['data_file_stem']}_{self.group_name}_"
                f"{halo_id}.npz"
            )
            np.savez(
                filepath / filename,
                total_histogram=total,
                edges=edges,
                cool_histogram=cool,
                warm_histogram=warm,
                hot_histogram=hot,
                halo_id=halo_id,
                halo_mass=halo_mass,
            )

        # plot
        self._plot_density_profile(
            halo_id,
            halo_mass,
            total,
            edges,
            hot,
            warm,
            cool,
        )

    def _restrict_gas_data_to_halo(
        self,
        gas_data: dict[str, NDArray],
        halo_id: int,
        halo_pos: NDArray,
        halo_radius: float,
        positions_tree: KDTree | None
    ) -> dict[str, NDArray]:
        """
        Restrict the given gas data only to the halo of the given ID.

        Appends to the gas data catalogue also the distance to the
        current halo center in units of virial radii.

        :param gas_data: The dictionary containing the gas data to
            constrain to only the particles within 2 R_vir of the given
            halo.
        :param halo_id: ID of the halo.
        :param halo_pos: The 3D cartesian vector giving the coordinates
            of the halo position. In units of kpc.
        :param halo_radius: The virial radius of the halo in units of
            kpc.
        :param positions_tree: If the neighboring particles must be
            queried from a KDTree, this must be the KDTree to use. If
            particle IDs already exist on file, this cna be None.
        :return: The dictionary of gas data, but only containing as
            values arrays, that have been restricted to particles within
            2 R_vir of the given halo. Additionally, also contains a new
            field 'Distances' which contains the distance of every gas
            particle to the halo position in units of virial radii.
        """
        neighbors = self._query_for_neighbors(
            halo_id,
            halo_pos,
            halo_radius,
            positions_tree,
            self.processes,
        )

        # restrict gas data to chosen particles only:
        restricted_gas_data = {}
        for field, value in gas_data.items():
            if field == "count":
                restricted_gas_data["count"] = len(neighbors)
                continue
            restricted_gas_data[field] = gas_data[field][neighbors]

        # calculate distances
        part_distances = np.linalg.norm(
            restricted_gas_data["Coordinates"] - halo_pos, axis=1
        ) / halo_radius
        assert np.max(part_distances) <= 2.0

        restricted_gas_data.update({"Distances": part_distances})

        return restricted_gas_data

    def _query_for_neighbors(
        self,
        halo_id: int,
        halo_position: NDArray,
        halo_radius: float,
        positions_tree: KDTree | None,
        workers: int
    ) -> NDArray:
        """
        Return the array of indices of particles within the given halo.

        The particles are queried either from the KDTree, or loaded from
        file. All particles within 2 R_vir are chosen and their indices
        in the list of particles is returned.

        :param halo_id: ID of the halo to query for particles.
        :param halo_position: The shape (3, ) array of the halo position
            in ckpc.
        :param halo_radius: The halo radius in units of ckpc.
        :param positions_tree: If ``self.use_tree`` is True and the
            particles are queried from an existing KDTree, this must be
            the KDTree. Otherwise, if no tree is required, this can be
            set to None.
        :param workers: The number of cores used to query the tree. If
            no tree is used, this can be arbitrarily set to 1.
        :return: The array of list indices of particles which belong to
            the chosen halo, i.e. are within 2 R_vir of the halo center.
        """
        # find all particles within 2 * R_vir
        if self.use_tree:
            neighbors = positions_tree.query_ball_point(
                halo_position,
                self.ranges[0][-1] * halo_radius,
                workers=workers
            )
            if self.to_file:
                logging.debug(
                    f"Saving particle indices and distances of halo {halo_id} "
                    "to file."
                )
                np.save(
                    self.part_id_dir
                    / f"particles_halo_{halo_id}{self.suffix}.npy",
                    neighbors
                )
        else:
            neighbors = np.load(
                self.part_id_dir / f"particles_halo_{halo_id}{self.suffix}.npy"
            )
        return neighbors

    def _plot_temperature_profile(
        self,
        halo_id: int,
        halo_mass: float,
        virial_temperature: float,
        histogram: NDArray,
        xedges: NDArray,
        yedges: NDArray,
    ) -> None:
        """
        Plot the temperature histogram of a single halo.

        :param halo_id: The halo ID.
        :param halo_mass: The mass of the halo in units of solar masses.
        :param virial_temperature: Virial temperature of the halo in Kelvin.
        :param histogram: The (N, N) shape array of the histogram data.
        :param xedges: The edges of the x bins.
        :param yedges: The edges of the y bins.
        """
        fig, axes = plt.subplots(figsize=(5, 4))
        fig.set_tight_layout(True)
        title = (
            f"{self.group_name.capitalize()} {halo_id} "
            rf"($10^{{{np.log10(halo_mass):.2f}}} M_\odot$)"
        )
        ranges = [xedges[0], xedges[-1], yedges[0], yedges[-1]]
        with np.errstate(invalid="ignore", divide="ignore"):
            if self.log:
                plot_radial_profiles.plot_2d_radial_profile(
                    fig,
                    axes,
                    histogram,
                    ranges,
                    title=title,
                    cbar_label="Normalized gas mass fraction (log10)",
                    cbar_limits=[-4.2, None],
                    scale="log",
                    cbar_ticks=[0, -1, -2, -3, -4],
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
        # virial temperature and temperature divisions
        if self.log:
            axes.hlines(
                np.log10(virial_temperature),
                xedges[0],
                xedges[-1],
                colors="blue"
            )
            plot_radial_profiles.overplot_temperature_divisions(
                axes, self.divisions, xedges[0], xedges[-1]
            )
        else:
            axes.hlines(
                virial_temperature, xedges[0], xedges[-1], colors="blue"
            )
            plot_radial_profiles.overplot_temperature_divisions(
                axes, 10**self.divisions, xedges[0], xedges[-1]
            )

        # save figure
        supplementary = f"{self.group_name}_{halo_id}"
        self._save_fig(
            fig,
            ident_flag=supplementary,
            subdirs=f"./{supplementary}",
        )

    def _plot_density_profile(
        self,
        halo_id: int,
        halo_mass: float,
        total_histogram: NDArray,
        edges: NDArray,
        hot_histogram: NDArray,
        warm_histogram: NDArray,
        cool_histogram: NDArray,
    ) -> None:
        """
        Plot the density histogram of a single halo.

        :param halo_id: ID of the halo to plot.
        :param halo_mass: Mass of the halo in solar masses.
        :param total_histogram: The histogram of density vs distance for
            all gas particles of the halo.
        :param edges: The edges of the histogram bins.
        :param hot_histogram: The histogram of density vs. distance only
            for the hot gas.
        :param warm_histogram: The histogram of density vs. distance only
            for the warm gas.
        :param cool_histogram: The histogram of density vs. distance only
            for the cool gas.
        :return: None
        """
        fig, axes = plt.subplots(figsize=(4, 4))
        fig.set_tight_layout(True)

        title = (
            f"{self.group_name.capitalize()} {halo_id} "
            rf"($10^{{{np.log10(halo_mass):.2f}}} M_\odot$)"
        )
        ranges = np.array([edges[0], edges[-1]])

        plot_radial_profiles.plot_1d_radial_profile(
            axes,
            total_histogram,
            edges,
            xlims=ranges,
            log=self.log,
            title=title,
        )
        # Removed: hot line is visually indistinguishable from total
        # plot_radial_profiles.plot_1d_radial_profile(
        #     axes,
        #     hot_histogram,
        #     edges,
        #     xlims=ranges,
        #     log=self.log,
        #     label=r"Hot ($> 10^{5.5} K$)",
        #     color=common.temperature_colors_named["hot"],
        # )
        plot_radial_profiles.plot_1d_radial_profile(
            axes,
            warm_histogram,
            edges,
            xlims=ranges,
            log=self.log,
            label=r"Warm ($10^{4.5} - 10^{5.5} K$)",
            color=common.temperature_colors_named["warm"],
        )
        plot_radial_profiles.plot_1d_radial_profile(
            axes,
            cool_histogram,
            edges,
            xlims=ranges,
            log=self.log,
            label=r"Cool ($< 10^{4.5} K$)",
            color=common.temperature_colors_named["cool"],
        )
        axes.legend(fontsize=10, frameon=False)

        # save
        supplementary = f"{self.group_name}_{halo_id}"
        self._save_fig(
            fig,
            ident_flag=supplementary,
            subdirs=f"./{supplementary}",
        )


class IndividualProfilesFromFilePipeline(IndividualRadialProfilePipeline):
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
        logging.info(f"Start loading {self.what} data from file.")
        if self.what == "temperature":
            load_generator = load_radial_profiles.load_individuals_2d_profile(
                self.paths["data_dir"] / f"temperature_profiles{self.suffix}",
                (self.radial_bins, self.temperature_bins),
            )
            plotting_func = self._plot_temperature_profile
        elif self.what == "density":
            load_generator = load_radial_profiles.load_individuals_1d_profile(
                self.paths["data_dir"] / f"density_profiles{self.suffix}",
                self.radial_bins,
            )
            plotting_func = self._plot_density_profile
        else:
            logging.fatal(f"Unrecognized plot type: {self.what}.")
            return 2

        # Step 2: plot data
        logging.info("Plotting individual halo profiles.")
        for halo_data in load_generator:
            if self.what == "temperature":
                halo_data.pop("original_histogram")
            plotting_func(**halo_data)
        logging.info("Done! Finished plotting individual halo profiles.")


class IndivTemperatureTNGClusterPipeline(IndividualRadialProfilePipeline):
    """
    Pipeline to create radial temperature profiles for TNG Cluster.

    Pipeline creates 2D histograms of the temperature distribution with
    radial distance to the center of the halo, including particles not
    bound to the halo. It does this for every halo above 10^14 solar
    masses in virial mass.

    This Pipeline is specific to the TNG Cluster simulation and utilizes
    some of the simulations properties to be more efficient than its
    parent class at handling particles.
    """

    def run(self) -> int:
        """
        Create radial temperature profiles for zoom-in cluster of TNG Cluster.

        Steps:

        1. Load halo data, restricted to zoom-ins.
        2. Calculate virial temperatures.
        3. For every cluster:
           1. Load gas cell data for temperature (only loading particles
              from the zoom).
           2. Calculate gas cell temperature, discard obsolete data.
           3. Load gas cell position data, calculate halocentric distance.
           4. Create a 2D histogram of temperature vs. distance. Let
              the histogram function handle particles beyond 2 R_200
              automatically.
           5. Save data and figure to file.
           6. Clean-up: discard all data for the current halo.

        :return: Exit code.
        """
        # Step 0: create directories, start monitoring, timing
        self._create_directories(
            subdirs=[f"temperature_profiles{self.suffix}"], force=True
        )
        tracemalloc.start()
        begin = time.time()

        # Step 1: Load and restrict halo data from TNG Cluster
        fields = [self.config.mass_field, self.config.radius_field, "GroupPos"]
        halo_data = halos_daq.get_halo_properties(
            self.config.base_path,
            self.config.snap_num,
            fields=fields,
            cluster_restrict=True,
        )
        timepoint = self._diagnostics(begin, "loading halo data", unit="kB")

        # Step 2: calculate virial temperatures
        halo_data["VirialTemperature"] = compute.get_virial_temperature(
            halo_data[self.config.mass_field],
            halo_data[self.config.radius_field],
        )
        timepoint = self._diagnostics(
            timepoint, "calculating virial temperature", unit="kB"
        )

        # Step 3: Loop through halos
        logging.info("Start processing individual halos.")
        for i, halo_id in enumerate(halo_data["IDs"]):
            logging.debug(f"Processing halo {halo_id} ({i}/352).")
            # Step 3.1: Load gas cell data for temperature
            gas_temperatures = gas_daq.get_cluster_temperature(
                halo_id,
                self.config.base_path,
                self.config.snap_num,
            )

            # Step 3.2: Load gas cell position data, calculate distance
            gas_data = gas_daq.get_gas_properties(
                self.config.base_path,
                self.config.snap_num,
                fields=["Coordinates", "Masses"],
                cluster=halo_id,
            )
            gas_distances = np.linalg.norm(
                gas_data["Coordinates"] - halo_data["GroupPos"][i], axis=1
            ) / halo_data[self.config.radius_field][i]

            # Step 3.3: Create histogram
            gas_data.update(
                {
                    "Temperatures": gas_temperatures,
                    "Distances": gas_distances,
                }
            )
            if self.what == "temperature":
                self._process_halo_temperature_profile(
                    halo_id,
                    halo_data["GroupPos"][i],
                    halo_data[self.config.mass_field][i],
                    halo_data[self.config.radius_field][i],
                    halo_data["VirialTemperature"][i],
                    gas_data,
                    None,
                )
            elif self.what == "density":
                self._process_halo_density_profile(
                    halo_id,
                    halo_data["GroupPos"][i],
                    halo_data[self.config.mass_field][i],
                    halo_data[self.config.radius_field][i],
                    gas_data,
                    None,
                )
            else:
                logging.fatal(f"Unrecognized plot type {self.what}.")
                return 3

            # Step 3.4: Save data to file and plot it

            # Step 3.5: Cleanup
            timepoint = self._diagnostics(
                timepoint, f"processing halo {halo_id} ({i}/352)"
            )

        self._diagnostics(begin, "total execution")
        tracemalloc.stop()
        return 0

    def _restrict_gas_data_to_halo(
        self,
        gas_data: dict[str, NDArray],
        halo_id: int,
        halo_pos: NDArray,
        halo_radius: float,
        positions_tree: KDTree | None
    ) -> dict[str, NDArray]:
        """
        Overwrites parent method, since no restriction is required.

        Method returns the ``gas_data`` unaltered since for TNG-Cluster,
        the gas data is already loaded only for the cluster.

        :param gas_data: Dictionary of gas data for the TNG-Cluster halo.
        :param halo_id: Dummy parameter.
        :param halo_pos: Dummy parameter.
        :param halo_radius: Dummy parameter.
        :param positions_tree: Dummy parameter.
        :return: ``gas_data``, unaltered (as it is already restricted).
        """
        return gas_data
