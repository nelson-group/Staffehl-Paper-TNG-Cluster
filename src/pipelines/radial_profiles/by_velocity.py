"""
Radial density profiles, split by radial gas velocity.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal

import matplotlib.pyplot as plt
import numpy as np

from library import compute
from library.config import config
from library.data_acquisition import gas_daq, halos_daq
from library.plotting import colormaps, common
from library.processing import selection, statistics
from pipelines import base

if TYPE_CHECKING:
    from numpy.typing import NDArray


@dataclass
class GenerateIndividualHistogramsPipeline(base.Pipeline):
    """
    Generate radial density profiles for clusters, split by velocity.

    Pipeline will go through all clusters of TNG300-1 and TNG-Cluster
    and create a density profile. The profile will consist of three
    histograms, each representing the density of gas within a specific
    range of radial velocity: inflowing, outflowing, or quasi-static.
    The histograms are saved to file.

    Note that this pipeline does not create plots for the individual
    profiles.
    """
    limiting_velocity: float  # either absolute value or fraction
    regime: Literal["cool", "warm", "hot", "total"]
    log: bool

    radial_bins: int = 50
    max_distance: float = 2.0  # in units of virial radii
    use_virial_velocity: bool = False

    n_clusters: ClassVar[int] = 632
    n_tng300: ClassVar[int] = 280
    n_tngclstr: ClassVar[int] = 352

    temperature_bins: ClassVar[tuple[float, ...]] = [0, 4.5, 5.5, np.inf]

    def __post_init__(self) -> None:
        super().__post_init__()

        # Check that a viable max distance was chosen
        if self.max_distance > 2.0:
            logging.fatal(
                "Cannot process any particles beyond 2 R_vir for TNG300-1. "
                "Please choose any maximum radius equal to or below 2.0. "
                "Canceling execution."
            )
            sys.exit(1)

        # simulation base paths
        self.tng300_basepath = config.get_simulation_base_path("TNG300-1")
        self.tngclstr_basepath = config.get_simulation_base_path("TNG-Cluster")

        # set data paths
        root = self.config.data_home / "radial_profiles" / "individuals"
        tng_300_dp = root / "TNG300_1" / "density_profiles" / "by_velocity"
        tng_clstr_dp = root / "TNG_Cluster" / "density_profiles" / "by_velocity"
        self.data_paths = {"TNG300_1": tng_300_dp, "TNG_Cluster": tng_clstr_dp}

    def run(self) -> int:
        """
        Save density profiles of clusters to file.

        Steps:

        1. Load group catalogue data for TNG300-1 halos, restrict to
           clusters.
        2. For every cluster in TNG300-1, create a density profile and
           save it to file.
        3. Load group catalogue data for TNG-Cluster original zoom-in
           clusters.
        4. For every cluster in TNG-Cluster, create a density profile
           and save it to file.

        :return: Exit code.
        """
        # Step 0: create directories, determine required groupcat fields
        if not (dpath := self.data_paths["TNG300_1"]).exists():
            logging.info(f"Creating missing data directory {dpath}.")
            dpath.mkdir(parents=True)
        if not (dpath := self.data_paths["TNG_Cluster"]).exists():
            logging.info(f"Creating missing data directory {dpath}.")
            dpath.mkdir(parents=True)
        fields = [self.config.mass_field, self.config.radius_field]
        virial_velocities = np.zeros(self.n_clusters)

        # Step 1: Load group data for TNG300-1
        _tmp = halos_daq.get_halo_properties(
            self.tng300_basepath, self.config.snap_num, fields
        )
        tng300_data = selection.select_clusters(
            _tmp, self.config.mass_field, expected_number=280
        )

        # Step 2: For every cluster in TNG300-1, create a density profile
        logging.info("Processing all clusters in TNG300-1.")
        for i, halo_id in enumerate(tng300_data["IDs"]):
            logging.debug(f"Processing TNG300 cluster {halo_id}.")
            virial_velocity = compute.get_virial_velocity(
                tng300_data[self.config.mass_field][i],
                tng300_data[self.config.radius_field][i],
            )
            gas_data = self._get_tng300_gas_data(halo_id)
            histograms, edges = self._get_profile_of_cluster(
                gas_data,
                tng300_data[self.config.radius_field][i],
                virial_velocity,
            )
            filename = (
                f"{self.paths['data_file_stem']}_TNG300_1_halo_{halo_id}.npz"
            )
            np.savez(
                self.data_paths["TNG300_1"] / filename,
                histograms=histograms,
                edges=edges,
                halo_id=halo_id,
                halo_mass=tng300_data[self.config.mass_field][i],
            )
            virial_velocities[i] = virial_velocity

        # clean-up
        del tng300_data, gas_data, histograms

        # Step 3: Load group data for TNG-Cluster
        fields.append("GroupPos")
        fields.append("GroupVel")
        tngclstr_data = halos_daq.get_halo_properties(
            self.tngclstr_basepath,
            self.config.snap_num,
            fields,
            cluster_restrict=True,
        )

        # Step 4: For every cluster in TNG-Cluster, create a density profile
        logging.info("Processing all clusters in TNG-Cluster.")
        for i, halo_id in enumerate(tngclstr_data["IDs"]):
            logging.debug(f"Processing TNG-Cluster cluster {halo_id}.")
            virial_velocity = compute.get_virial_velocity(
                tngclstr_data[self.config.mass_field][i],
                tngclstr_data[self.config.radius_field][i],
            )
            gas_data = self._get_tngclstr_gas_data(
                halo_id,
                tngclstr_data["GroupPos"][i],
                tngclstr_data["GroupVel"][i],
                tngclstr_data[self.config.radius_field][i],
            )
            histograms, edges = self._get_profile_of_cluster(
                gas_data,
                tngclstr_data[self.config.radius_field][i],
                virial_velocity,
            )
            filename = (
                f"{self.paths['data_file_stem']}_TNG_Cluster_cluster_{halo_id}.npz"
            )
            np.savez(
                self.data_paths["TNG_Cluster"] / filename,
                histograms=histograms,
                edges=edges,
                halo_id=halo_id,
                halo_mass=tngclstr_data[self.config.mass_field][i],
            )
            virial_velocities[self.n_tng300 + i] = virial_velocity

        # Save virial velocities to file
        np.save(
            self.config.data_home / "clusters_virial_velocities.npy",
            virial_velocities
        )

        return 0

    def _get_profile_of_cluster(
        self,
        gas_data: dict[str, NDArray],
        cluster_radius: float,
        virial_velocity: float,
    ) -> tuple[NDArray, NDArray]:
        """
        Return the density profile for the cluster gas data given.

        Function takes a data dictionary, which must contain these
        following keys:

        - Temperatures
        - Distances
        - Masses
        - RadialVelocities

        Each of these keys must have as a corresponding value the 1D
        array of these quantities for every gas cell within the halo
        of the cluster. The function then uses them to find the radial
        density profile of the cluster, both as a total and as three
        profiles for only inflowing, outflowing and quasi-static gas
        respectively. The result is returned.

        :param gas_data: The dictionary of the gas data for all gas cells
            of the current cluster. All values must be arrays of shape
            (N, ) where N is the number of gas cells in the cluster.
        :param cluster_radius: The radius of the cluster in kpc.
        :param virial_velocity: Virial velocity of the cluster in km/s.
        :return: The density profiles of the cluster, of shape (4, R)
            where R is the number of radial bins and the first axis
            separates the total, inflowing, quasi-static, and outflowing
            gas density profiles respectively, as well as the edges of
            the histogram bins as an array of shape (R + 1, ).
        """
        if self.use_virial_velocity:
            # normalise radial velocities to virial velocities
            gas_data["RadialVelocities"] /= virial_velocity
            if 0 < self.limiting_velocity <= 1:
                limit = self.limiting_velocity
            else:
                logging.warning(
                    "Was instructed to use virial velocities, but the limiting"
                    " velocity for the quasi-static regime was not given as "
                    "a fraction of the virial velocity. Please use a value "
                    "between 0 and 1 as limiting velocity when using virial "
                    "velocities."
                )
                logging.info(
                    "Arbitrarily choosing 10% of virial velocity as limit."
                )
                limit = 0.1
        else:
            limit = self.limiting_velocity  # in km/s
        ranges = np.array([0, self.max_distance])
        # translator for temperature regime to mask index
        regime_to_index = {"cool": 1, "warm": 2, "hot": 3}
        # mask gas to only current regime
        if self.regime != "total":
            temperature_mask = np.digitize(
                np.log10(gas_data["Temperatures"]), self.temperature_bins
            )
            gas_data = selection.mask_data_dict(
                gas_data, temperature_mask, regime_to_index[self.regime]
            )

        # create the histograms for total, inflow, quasi-static and outflow
        total_hist, edges = statistics.volume_normalized_radial_profile(
            gas_data["Distances"],
            gas_data["Masses"],
            self.radial_bins,
            cluster_radius,
            radial_range=ranges,
        )
        inflow_mask = np.where(gas_data["RadialVelocities"] <= -limit)
        inflow_hist, _ = statistics.volume_normalized_radial_profile(
            gas_data["Distances"][inflow_mask],
            gas_data["Masses"][inflow_mask],
            self.radial_bins,
            cluster_radius,
            radial_range=ranges,
        )
        static_mask = np.where(np.abs(gas_data["RadialVelocities"]) < limit)
        static_hist, _ = statistics.volume_normalized_radial_profile(
            gas_data["Distances"][static_mask],
            gas_data["Masses"][static_mask],
            self.radial_bins,
            cluster_radius,
            radial_range=ranges,
        )
        outflow_mask = np.where(gas_data["RadialVelocities"] >= limit)
        outflow_hist, _ = statistics.volume_normalized_radial_profile(
            gas_data["Distances"][outflow_mask],
            gas_data["Masses"][outflow_mask],
            self.radial_bins,
            cluster_radius,
            radial_range=ranges,
        )

        # package and return results
        histograms = np.array(
            [total_hist, inflow_hist, static_hist, outflow_hist]
        )
        return histograms, edges

    def _get_tng300_gas_data(self, halo_id: int) -> dict[str, NDArray]:
        """
        Return the gas data restricted to only the chosen cluster region.

        The function loads the particle data for all particles within
        two virial radii and possibly reduces it to only those particles
        within the region specified (default is all particles within the
        two virial radii).

        Returned dictionary will have only the keys required for further
        processing:

        - Temperatures
        - Distances
        - Masses
        - RadialVelocities

        :param halo_id: ID of the cluster.
        :return: The dictionary containing the gas data of the cluster.
        """
        # load data from file
        gas_temperatures = np.load(
            self.config.data_home / "particle_temperatures" / "TNG300_1"
            / f"particle_temperatures_halo_{halo_id}.npy"
        )
        gas_distances = np.load(
            self.config.data_home / "particle_distances" / "TNG300_1"
            / f"particle_distances_halo_{halo_id}.npy"
        )
        gas_masses = np.load(
            self.config.data_home / "particle_masses" / "TNG300_1"
            / f"gas_masses_halo_{halo_id}.npy"
        )
        radial_velocities = np.load(
            self.config.data_home / "particle_velocities" / "TNG300_1"
            / f"radial_velocity_halo_{halo_id}.npy"
        )

        # if the max distance is set to 2R_vir, the data is already complete
        if self.max_distance == 2.0:
            gas_data = {
                "Temperatures": gas_temperatures,
                "Distances": gas_distances,
                "Masses": gas_masses,
                "RadialVelocities": radial_velocities,
            }
            return gas_data

        # otherwise restrict the data to only the given distance
        mask = np.where(gas_distances <= self.max_distance)
        gas_data = {
            "Temperatures": gas_temperatures[mask],
            "Distances": gas_distances[mask],
            "Masses": gas_masses[mask],
            "RadialVelocities": radial_velocities[mask],
        }
        return gas_data

    def _get_tngclstr_gas_data(
        self,
        halo_id: int,
        halo_pos: NDArray,
        halo_vel: NDArray,
        halo_radius: float,
    ) -> dict[str, NDArray]:
        """
        Return the gas data restricted to only the chosen cluster region.

        The function loads the particle data for all particles of the
        original zoom-region and reduces it to only those particles
        within the region specified (default is all particles within
        two virial radii).

        Returned dictionary will have only the keys required for further
        processing:

        - Temperatures
        - Distances
        - Masses
        - RadialVelocities

        :param halo_id: ID of the cluster.
        :return: The dictionary containing the gas data of the cluster.
        """
        # load data from catalogue
        gas_temperatures = gas_daq.get_cluster_temperature(
            self.tngclstr_basepath,
            self.config.snap_num,
            halo_id,
        )
        fields = ["Coordinates", "Masses", "Velocities"]
        gas_data = gas_daq.get_gas_properties(
            self.tngclstr_basepath,
            self.config.snap_num,
            fields=fields,
            cluster=halo_id,
        )

        # calculate derived quantities
        gas_distances = np.linalg.norm(
            gas_data["Coordinates"] - halo_pos, axis=1
        ) / halo_radius
        radial_velocities = compute.get_radial_velocities(
            halo_pos,
            halo_vel,
            gas_data["Coordinates"],
            gas_data["Velocities"],
        )

        # restrict data to only the particles within range
        mask = np.where(gas_distances <= self.max_distance)

        # construct result array
        final_data = {
            "Temperatures": gas_temperatures[mask],
            "Distances": gas_distances[mask],
            "Masses": gas_data["Masses"][mask],
            "RadialVelocities": radial_velocities[mask],
        }
        return final_data


class PlotMeanProfilesPipeline(GenerateIndividualHistogramsPipeline):
    """
    Plot mean density profile of the different velocity regimes.

    Pipeline loads data previously created for individual clusters and
    finds, for inflowing, quasi-static, outflowing and all gas, the
    mean density profile and plots it, including a shaded error region.
    """

    def run(self) -> int:
        """
        Plot the mean density profile of the given temperature regime.

        :return: Exit code.
        """
        # Step 1: Load histograms from file
        histograms, edges, _ = self._load_individual_histograms()

        # Step 2: stack the histograms per flow direction
        logging.info("Calculating mean and std of histograms.")
        means = np.nanmean(histograms, axis=0)
        stds = np.nanstd(histograms, axis=0)

        # Step 3: plot
        self._plot_means(means, stds, edges)
        return 0

    def _load_individual_histograms(self) -> tuple[NDArray, NDArray, NDArray]:
        """
        Load the individual histograms of all clusters from file.

        Method returns an array of 632 histograms for all the clusters
        in TNG300 and TNG-Cluster, in the specified temperature regime.

        In case of an error (missing files, files contain wrong data),
        the method terminates the pipeline execution after logging an
        error message.

        :return: Tuple of the histograms of shape (632, 4, R) where R is
            the number of radial bins, and the second axis splits them
            into the total, inflowing, quasi-static, and outflowing gas,
            as well as the edges of the bins of shape (R, ), and the halo
            masses in solar masses as array of shape (632, ).
        """
        # verify directories and that files exist
        if not self.data_paths["TNG300_1"].exists():
            logging.fatal("Data files for TNG300-1 do not exist.")
            sys.exit(1)
        if not self.data_paths["TNG_Cluster"].exists():
            logging.fatal("Data files for TNG-Cluster do not exist.")
            sys.exit(1)

        # check that all files exist, create a list of required files
        files = self.data_paths["TNG300_1"].iterdir()
        tng_300_name = f"{self.paths['data_file_stem']}_TNG300_1_halo_"
        tng_300_files = [f for f in files if tng_300_name in f.stem]
        if len(tng_300_files) != self.n_tng300:
            logging.fatal("Data files for TNG300-1 are incomplete.")
            sys.exit(2)
        # and the same for TNG-Cluster
        files = self.data_paths["TNG_Cluster"].iterdir()
        tng_clstr_name = f"{self.paths['data_file_stem']}_TNG_Cluster_cluster_"
        tng_clstr_files = [f for f in files if tng_clstr_name in f.stem]
        if len(tng_clstr_files) != self.n_tngclstr:
            logging.fatal("Data files for TNG-Cluster are incomplete.")
            sys.exit(2)

        # Load histograms of current regime
        logging.info("Loading histograms for density profiles from file.")
        histograms = np.ones((self.n_clusters, 4, self.radial_bins))
        masses = np.zeros(self.n_clusters)
        edges = np.linspace(0, self.max_distance, num=self.radial_bins + 1)
        # TNG300-1
        for i, filename in enumerate(tng_300_files):
            with np.load(filename, "r") as data_file:
                masses[i] = data_file["halo_mass"]
                # unpack the histograms
                histograms[i] = data_file["histograms"]
                # test that the edges line up
                np.testing.assert_allclose(
                    data_file["edges"],
                    edges,
                    rtol=1e-4,
                )
        # TNG-Cluster
        for i, filename in enumerate(tng_clstr_files):
            with np.load(filename, "r") as data_file:
                masses[i + self.n_tng300] = data_file["halo_mass"]
                # unpack the histograms
                histograms[i + self.n_tng300] = data_file["histograms"]
                # test that the edges line up
                np.testing.assert_allclose(
                    data_file["edges"],
                    edges,
                    rtol=1e-4,
                )

        # Return the histograms and edges
        return histograms, edges, masses

    def _plot_means(
        self, means: NDArray, stds: NDArray, edges: NDArray
    ) -> None:
        """
        Plot the density plots as line plots with error region.

        :param means: Array of mean densities per radial bin. Shape
            (4, R) where the first axis divides the histograms into
            total, inflowing, quasi-static, and outflowing gas
            respectively, and the second axis denotes the radial bins.
        :param stds: Array of standard deviation for the means per
            radial bin. Shape (4, R) where the first axis divides the
            histograms into total, inflowing, quasi-static, and outflowing
            gas respectively, and the second axis denotes the radial bins.
        :param edges: The edges of the radial bins.
        :return: None, saves figure to file.
        """
        logging.info("Plotting density profiles split by velocity.")
        fig, axes = plt.subplots(figsize=(4, 4))
        axes.set_xlabel(r"Distance from halo center [$R_{200c}$]")
        axes.set_ylabel(r"Gas density [$M_\odot / kpc^3$]")
        # adjust y-axis scale and limits
        if self.regime == "hot":
            ylim = (5, 1e6)
        else:
            ylim = (1e-2, 1e5)
        axes.set_ylim(ylim)
        axes.set_xlim((0, self.max_distance))
        if self.log:
            axes.set_yscale("log")

        # create x-values (middle of bins)
        xs = (edges[:-1] + edges[1:]) / 2

        # plot the different velocity regimes
        common.plot_curve_with_error_region(
            xs,
            means[0],
            None,
            np.array([stds[0], stds[0]]),
            axes,
            linestyle="solid",
            label="Total",
            suppress_error_region=False,
            suppress_error_line=True,
        )
        common.plot_curve_with_error_region(
            xs,
            means[1],
            None,
            np.array([stds[1], stds[1]]),
            axes,
            linestyle="solid",
            label="Inflowing",
            color="dodgerblue",
            suppress_error_region=False,
            suppress_error_line=True,
        )
        unit = r"v_{\rm vir}" if self.use_virial_velocity else r"{\rm km/s}"
        vmax = self.limiting_velocity
        common.plot_curve_with_error_region(
            xs,
            means[2],
            None,
            np.array([stds[2], stds[2]]),
            axes,
            linestyle="solid",
            label=rf"Quasi-static ($|v_r| \leq {vmax:.1f} {unit}$)",
            color="limegreen",
            suppress_error_region=False,
            suppress_error_line=True,
        )
        common.plot_curve_with_error_region(
            xs,
            means[3],
            None,
            np.array([stds[3], stds[3]]),
            axes,
            linestyle="solid",
            label="Outflowing",
            color="crimson",
            suppress_error_region=False,
            suppress_error_line=True,
        )

        # legend
        axes.legend()

        # save figure
        logging.info("Saving figure to file.")
        self._save_fig(fig, ident_flag=str(self.limiting_velocity))


class PlotFlowRatioHistograms(PlotMeanProfilesPipeline):
    """
    Plot ratio of inflow over outflow binned by halo mass.

    Pipeline loads data previously created for individual clusters and
    finds, for all clusters in mass bins of 0.2 dex, the density ratio
    of inflowing vs. outflowing gas at every radius and plots it as a
    curve.
    """

    def run(self) -> int:
        """
        Plot the ratio of inflow over outflow in halo mass bins.

        :return: Exit code.
        """
        # Step 1: load individual histograms
        histograms, edges, masses = self._load_individual_histograms()

        # Step 2: bin histograms by mass
        mass_bins = 10**np.linspace(14, 15.4, num=8)
        mask = selection.digitize_clusters(masses, mass_bins)

        # Step 3: compute ratios of in- and outflow
        mean_ratios = np.zeros((8, self.radial_bins))
        for i in range(7):
            bin_index = i + 1
            mean_inflow = np.nanmean(
                histograms[:, 1][mask == bin_index], axis=0
            )
            mean_total = np.nanmean(
                histograms[:, 0][mask == bin_index], axis=0
            )
            # Mask in- and outflow only to current mass bin, find ratio
            mean_ratios[i] = mean_inflow / mean_total
        # add the mean ratio over all clusters
        total_mean_inflow = np.nanmean(histograms[:, 1], axis=0)
        total_mean_total = np.nanmean(histograms[:, 0], axis=0)
        mean_ratios[-1] = total_mean_inflow / total_mean_total

        # Step 3: plot
        self._plot_ratios(mean_ratios, edges, mass_bins)
        return 0

    def _plot_ratios(
        self, mean_ratios: NDArray, edges: NDArray, mass_bin_edges: NDArray
    ) -> None:
        """
        Plot the mean ratio of inflow over total gas density.

        :param mean_ratios: The array of ratios of inflow histograms over
            outflow histograms of shape (8, R) where R is the number of
            radial bins. The last entry is the total over all clusters,
            the first seven are in mass bins of 0.2 dex.
        :param edges: The edges of the radial bins.
        :return: None
        """
        logging.info("Plotting density ratios of inflow over outflow.")
        fig, axes = plt.subplots(figsize=(4, 4))
        axes.set_xlabel(r"Distance from halo center [$R_{200c}$]")
        axes.set_ylabel(r"Density ratio (inflowing/total)")

        if self.log:
            axes.set_yscale("log")

        # create x-values (middle of bins)
        xs = (edges[:-1] + edges[1:]) / 2

        # plot the different velocity regimes
        for i in range(8):
            if i == 7:
                color = "black"
                label = "Total"
            else:
                color = colormaps.sample_cmap("jet", 7, i)
                label = (
                    rf"$10^{{{np.log10(mass_bin_edges[i]):.1f}}} - "
                    rf"10^{{{np.log10(mass_bin_edges[i + 1]):.1f}}} M_\odot$"
                )
            common.plot_curve_with_error_region(
                xs,
                mean_ratios[i],
                x_err=None,
                y_err=None,
                axes=axes,
                linestyle="solid",
                color=color,
                label=label,
                zorder=0,
                suppress_error_line=True,
                suppress_error_region=True,
            )

        # legend
        axes.legend(ncols=2, fontsize=8)

        # save figure
        logging.info("Saving figure to file.")
        self._save_fig(
            fig, ident_flag=f"binned_ratios_{self.limiting_velocity}"
        )
