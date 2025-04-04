"""
Pipeline for stacking radial profiles.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal

import matplotlib.cm
import matplotlib.colors
import matplotlib.lines
import matplotlib.pyplot as plt
import numpy as np

from library.loading import load_radial_profiles
from library.plotting import colormaps, common, plot_radial_profiles, pltutil
from library.processing import selection, statistics
from pipelines.base import Pipeline

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure
    from numpy.typing import NDArray


@dataclass
class StackProfilesBinnedPipeline(Pipeline):
    """
    Pipeline to create stacks of radial profiles, binned by halo mass.

    Requires the data files for profiles of TNG300-1 and TNG Cluster to
    already exist, since they will be loaded.
    """

    log: bool
    what: str
    method: Literal["mean", "median"]
    core_only: bool = False
    normalize: bool = True

    # edges of mass bins to use (0.2 dex width)
    mass_bins: ClassVar[NDArray] = 10**np.arange(14.0, 15.4, 0.2)
    n_clusters: ClassVar[int] = 632  # total number of clusters

    def __post_init__(self):
        super().__post_init__()
        if self.core_only:
            self.suffix = "_core"
        else:
            self.suffix = ""
        if not self.normalize:
            self.suffix += "_absolute"

    def run(self) -> int:
        """
        Plot radial profiles for different mass bins of the given type.

        This will load the existing profile data from both TNG300-1 and
        TNG Cluster and bin the histograms by halo mass. Then, plots
        will be created, containing the mean or median profile within
        each mass bin, plus a total profile, containing all clusters of
        both simulations (i.e. halos with log M > 14).

        Temperature will be plotted as two 2x4 plots of 2D histograms,
        the first one will show the mean/median temperature profile in
        the 7 mass bins, plus the total across all mass bins; the second
        will show the error. Density will be plotted as one single plot
        with eight lines (including confidence region) which represent
        the seven mass bins (colored) and the total (black), as well as
        eight dotted lines showing the same information but only for the
        cool gas of the clusters.

        Steps:

        1. Allocate space in memory for histogram data.
        2. Load halo data for TNG300-1
        3. Load halo data for TNG Cluster
        4. Create a mass bin mask.
        5. Allocate memory space for mean/median histograms per mass bin
           and a total over all mass bins (8 histograms)
        6. For every mass bin:
           1. Mask histogram data
           2. Calculate mean/median profile with errors
           3. Place data in allocated memory
        7. Create a total mean/median histogram with errors; place it
           into allocated memory.
        8. Save data to file.
        9. Plot the data according to chosen type.

        :return: Exit code.
        """
        # Step 0: verify directories
        self._verify_directories()

        if self.core_only:
            logging.info("Was asked to stack data only for cluster cores.")

        # Step 1 - 3: Load data (depends on what type of data, so this
        # task is split):
        logging.info("Loading halo data from TNG300-1 and TNG Cluster.")
        if self.what == "temperature":
            cluster_data = self._load_temperature_hists()
        elif self.what == "density":
            cluster_data = self._load_density_hists()
        else:
            logging.fatal(f"Unrecognized plot type {self.what}. Aborting.")
            return 1
        # unpack
        cluster_masses = cluster_data["masses"]
        cluster_histograms = cluster_data["histograms"]
        edges = cluster_data["edges"]

        # Step 4: create mass bin mask
        logging.info("Creating a mass bin mask.")
        n_mass_bins = len(self.mass_bins) - 1
        mask = selection.digitize_clusters(cluster_masses, self.mass_bins)

        # Step 5: allocate memory for mean/median histograms
        # cluster_histograms[0].shape will be one of the following:
        # - (50, 50) for temperature (shape of histogram)
        # - (2, 50) for density (being split into total and only cool gas)
        stacks = np.zeros((n_mass_bins + 1, ) + cluster_histograms[0].shape)
        """First 7 entries: mass bins. Last entry: total."""
        errors = np.zeros(
            (n_mass_bins + 1, 2,) + cluster_histograms[0].shape
        )  # yapf: disable
        """Axes: mass bin, lower/upper error, values"""

        # Step 6: loop over mass bins and create stacks
        logging.info("Start stacking histograms per mass bin.")
        if self.what == "temperature":
            stacking_func = self._stack_temperature_hists
        else:
            stacking_func = self._stack_density_hists
        # loop over mass bins
        for i in range(n_mass_bins):
            # mask histogram data
            hists_in_bin = selection.mask_quantity(
                cluster_histograms, mask, index=(i + 1)
            )
            stacks[i], errors[i] = stacking_func(hists_in_bin)

        # Step 7: create a total mean/median profile
        stacks[-1], errors[-1] = stacking_func(cluster_histograms)

        # Step 8: save data to file
        if self.to_file:
            pass  # not implemented

        # Step 9: plot the data
        logging.info(f"Plotting {self.what} {self.method} stacks.")
        if self.what == "temperature":
            f, _ = self._plot_temperature_stacks(stacks, errors, edges)
        else:
            f, _ = self._plot_density_stacks(stacks, errors, edges)
        logging.info(f"Saving {self.what} {self.method} profile to file.")
        self._save_fig(f, ident_flag=self.method)

        return 0

    def _load_temperature_hists(self) -> dict[str, NDArray]:
        """
        Load temperature histograms, edges and halo masses.

        The returned values are a concatenation of TNG300-1 and TNG
        Cluster data, in that order (meaning the first 280 entries of
        the data arrays of the 'masses' and 'histograms' fields belong
        to TNG300-1, the remaining 352 belong to TNG Cluster).

        :return: Mapping of loaded data. Has as keys 'masses', 'histograms',
            and 'edges', with the values representing the cluster masses,
            the temperature histograms and the edges of the histograms
            for all clusters of TNG300-1 and TNG Cluster.
        """
        # determine shape and edges
        test_path = (
            self.paths["data_dir"] / "TNG300_1"
            / f"temperature_profiles{self.suffix}"
        )
        filepath = list(test_path.iterdir())[0]
        with np.load(filepath.resolve()) as test_file:
            shape = test_file["original_histogram"].shape
            edges = np.array(
                [
                    test_file["xedges"][0],
                    test_file["xedges"][-1],
                    test_file["yedges"][0],
                    test_file["yedges"][-1],
                ]
            )

        # allocate memory
        masses = np.zeros(self.n_clusters)
        hists = np.zeros((self.n_clusters, ) + shape)

        # load TNG300-1 data
        load_generator = load_radial_profiles.load_individuals_2d_profile(
            self.paths["data_dir"] / "TNG300_1"
            / f"temperature_profiles{self.suffix}",
            shape,  # automatically validates shapes
        )
        n_tng300_clusters = 0
        for i, halo_data in enumerate(load_generator):
            masses[i] = halo_data["halo_mass"]
            hists[i] = halo_data["original_histogram"]
            n_tng300_clusters += 1

        # load TNG Cluster data and verify it
        load_generator = load_radial_profiles.load_individuals_2d_profile(
            self.paths["data_dir"] / "TNG_Cluster"
            / f"temperature_profiles{self.suffix}",
            shape,  # automatically validates shapes
        )
        for i, halo_data in enumerate(load_generator):
            if i == 0:
                # verify both simulation data were saved with the same shape
                cledges = np.array(
                    [
                        halo_data["xedges"][0],
                        halo_data["xedges"][-1],
                        halo_data["yedges"][0],
                        halo_data["yedges"][-1],
                    ]
                )
                if not np.allclose(edges, cledges):
                    logging.fatal(
                        f"Temperature histograms for TNG300-1 and TNG-Cluster "
                        f"have different bin edges:\nTNG300: {edges}\n"
                        f"TNG-Cluster: {cledges}"
                    )
                    sys.exit(2)
            masses[i + n_tng300_clusters] = halo_data["halo_mass"]
            hists[i + n_tng300_clusters] = halo_data["original_histogram"]

        # construct and return mapping
        return {"masses": masses, "histograms": hists, "edges": edges}

    def _load_density_hists(self) -> dict[str, NDArray]:
        """
        Load density histograms, edges and halo masses.

        The returned values are a concatenation of TNG300-1 and TNG
        Cluster data, in that order (meaning the first 280 entries of
        the data arrays of the 'masses' and 'histograms' fields belong
        to TNG300-1, the remaining 352 belong to TNG Cluster).

        .. attention::
            The field 'histograms' perhaps unexpectedly has shape (2, N).
            This is due to the fact that the first entry of this array
            is the total histogram for all gas and the second entry is
            the density profile of the cool gas only!

        :return: Mapping of loaded data. Has as keys 'masses', 'histograms',
            and 'edges', with the values representing the cluster masses,
            the density histograms and the edges of the histograms for
            all clusters of TNG300-1 and TNG Cluster.
        """
        # determine shape and edges
        test_path = (
            self.paths["data_dir"] / "TNG300_1"
            / f"density_profiles{self.suffix}"
        )
        filepath = list(test_path.iterdir())[0]
        with np.load(filepath.resolve()) as test_file:
            shape = test_file["total_inflow"].shape
            edges = np.array(test_file["edges"])

        # allocate memory
        masses = np.zeros(self.n_clusters)
        hists = np.zeros((self.n_clusters, 2, ) + shape)  # yapf: disable

        # load TNG300-1 data
        load_generator = load_radial_profiles.load_individuals_1d_profile(
            self.paths["data_dir"] / "TNG300_1"
            / f"density_profiles{self.suffix}",
            shape,  # automatically validates shapes
        )
        n_tng300_clusters = 0
        for i, halo_data in enumerate(load_generator):
            masses[i] = halo_data["halo_mass"]
            hists[i][0] = (
                halo_data["total_inflow"] + halo_data["total_outflow"]
            )
            hists[i][1] = halo_data["cool_inflow"] + halo_data["cool_outflow"]
            n_tng300_clusters += 1

        # load TNG Cluster data and verify it
        load_generator = load_radial_profiles.load_individuals_1d_profile(
            self.paths["data_dir"] / "TNG_Cluster"
            / f"density_profiles{self.suffix}",
            shape,  # automatically validates shapes
        )
        for i, halo_data in enumerate(load_generator):
            if i == 0:
                if not np.allclose(halo_data["edges"], edges):
                    logging.fatal(
                        f"Density histograms for TNG300-1 and TNG-Cluster "
                        f"have different bin edges:\nTNG300: {edges}\n"
                        f"TNG-Cluster: {halo_data['edges']}"
                    )
                    sys.exit(2)
            masses[i + n_tng300_clusters] = halo_data["halo_mass"]
            hists[i + n_tng300_clusters][0] = (
                halo_data["total_inflow"] + halo_data["total_outflow"]
            )
            hists[i + n_tng300_clusters][1] = (
                halo_data["cool_inflow"] + halo_data["cool_outflow"]
            )

        # construct and return mapping
        return {"masses": masses, "histograms": hists, "edges": edges}

    def _stack_temperature_hists(
        self,
        histograms: NDArray,
    ) -> tuple[NDArray, NDArray]:
        """
        Stack the temperature histograms given according to self.method.

        The method returns as first array the stack, and as second array
        a shape (2, X, Y) array, where X, Y is the shape of the temperature
        histogram array. The first entry is the lower error and the
        second entry is the upper error.

        Note that the stacks are being column-wise normalised such that
        the sum of every column is always unity.

        :param histograms: The array of histograms of shape (X, Y) to
            stack.
        :return: Tuple of the stacked histogram (shape (X, Y)) and the
            error (shape (2, X, Y)).
        """
        stack, low_err, upp_err = statistics.stack_histograms(histograms, self.method)
        # column-normalise the stack
        stack_normalized, _, _ = statistics.column_normalized_hist2d(
            stack, None, None, normalization="density"
        )
        return stack_normalized, np.array([low_err, upp_err])

    def _stack_density_hists(
        self,
        histograms: NDArray,
    ) -> tuple[NDArray, NDArray]:
        """
        Stack the temperature histograms given according to self.method.

        The method expects ``histograms`` to be an array of shape
        (N, 2, X) where N is the number of clusters in the mass bin to
        stack, and X is the number of radial bins. The second axis is
        supposed to split the histograms into a total profile (index 0)
        and a cool-gas-only profile (index 1), i.e. ``histograms[i][0]``
        selects the total density profile of the i-th cluster, while
        ``histograms[i][1]`` selects the density profile for only the
        cool gas of the i-th halo.

        :param histograms: The array of histograms of shape (2, X) to
            stack. Along the first axis, the first entry must be the
            total gas density profile, the second entry must be the
            cool-gas-only density profile.
        :return: Tuple of the stacked histogram (shape (2, Y)) and the
            error (shape (2, 2, X)). For the error, the axes are
            assigned as (total/cool gas, lower/upper error, bins).
        """
        # splice input array [all halos, total/cool only, full histogram]
        total_hists = histograms[:, 0, :]
        cool_gas_hists = histograms[:, 1, :]
        # stack arrays separately
        total_stack, total_lowerr, total_upperr = statistics.stack_histograms(
            total_hists, self.method
        )
        cool_stack, cool_lowerr, cool_upperr = statistics.stack_histograms(
            cool_gas_hists, self.method
        )
        # construct expected return array shape
        stack = np.array([total_stack, cool_stack])
        errors = np.array(
            [[total_lowerr, total_upperr], [cool_lowerr, cool_upperr]]
        )
        return stack, errors

    def _plot_temperature_stacks(
        self, stacks: NDArray, errors: NDArray, edges: NDArray
    ) -> tuple[Figure, Axes]:
        """
        Plot the stacked temperature profiles.

        :param stacks: The array of N + 1 stacked temperature histograms,
            where N is the number of mass bins. Histograms are expected
            to be 2D of shape (X, Y). Last entry of the array (i.e. with
            index N + 1) is expected to be the total stack.
        :param errors: The array of errors on the stacks, of shape
            (N + 1, 2, X, Y).
        :param edges: The edges of the histograms, [xmin, xmax, ymin, ymax].
        :return: The figure and axes with the plot.
        """
        ncols = len(stacks) // 2
        fig, axes = plt.subplots(
            nrows=2,
            ncols=ncols,
            figsize=(ncols * 1.8 + 1.2, 4),
            sharex=True,
            sharey=True,
            gridspec_kw={"hspace": 0, "wspace": 0},
            layout="constrained",
        )
        # fig.set_tight_layout(True)
        flat_axes = axes.flatten()
        # common axes labels
        if self.normalize:
            fig.supxlabel(r"Distance from halo center [$R_{200c}$]")
        else:
            fig.supxlabel("Distance from halo center [kpc]")
        fig.supylabel(r"Temperature [$\log K$]")

        xrange = edges[1] - edges[0]
        if self.log:
            clabel = r"Normalized mean gas fraction ($\log_{10}$)"
            value_range = (-4, np.log10(np.max(stacks)))
            text_pos = (0.05 * xrange, 3.3)
        else:
            clabel = "Normalized mean gas fraction"
            value_range = (np.min(stacks), np.max(stacks))
            text_pos = (0.05 * xrange, 1e5)

        for i in range(len(stacks)):
            # plot histograms
            with np.errstate(invalid="ignore", divide="ignore"):
                plot_radial_profiles.plot_2d_radial_profile(
                    fig,
                    flat_axes[i],
                    stacks[i],
                    edges,
                    xlabel=None,
                    ylabel=None,
                    cbar_label=clabel,
                    cbar_limits=[-4, None] if self.log else None,
                    scale="log" if self.log else "linear",
                    value_range=value_range,
                    suppress_colorbar=True,
                )
            # running average
            running_average = statistics.get_2d_histogram_running_average(
                stacks[i], edges[-2:]
            )
            plot_radial_profiles.overplot_running_average(
                fig,
                flat_axes[i],
                running_average,
                edges,
                suppress_label=True,
            )
            # label with mass bin
            if i == len(stacks) - 1:
                label = "Total"
            else:
                label = (
                    rf"$10^{{{np.log10(self.mass_bins[i]):.1f}}} - "
                    rf"10^{{{np.log10(self.mass_bins[i + 1]):.1f}}} M_\odot$"
                )
            flat_axes[i].text(*text_pos, label, color="white")
            # add temperature divisions
            plot_radial_profiles.overplot_temperature_divisions(
                flat_axes[i], [4.5, 5.5], edges[0], edges[1]
            )

        # add a colorbar
        norm = matplotlib.colors.Normalize(*value_range)
        fig.colorbar(
            matplotlib.cm.ScalarMappable(norm=norm, cmap="inferno"),
            ax=axes.ravel().tolist(),
            aspect=20,
            pad=0.03,
            label=clabel,
            extend="min" if self.log else "neither",
        )
        return fig, axes

    def _plot_density_stacks(
        self, stacks: NDArray, errors: NDArray, edges: NDArray
    ) -> tuple[Figure, Axes]:
        """
        Plot the stacked density profiles.

        :param stacks: The array of N + 1 stacked density histograms,
            where N is the number of mass bins. Histograms are expected
            to be 2D of shape (2, X). The first axes splits the 1D
            histograms into total stack (first entry) and cool gas only
            stack (second entry). X is the number of radial bins.
        :param errors: The array of errors on the stacks, of shape
            (N + 1, 2, 2, X). This corresponds to the following quantities:
            (bin/total, total/cool-only, lower/upper error, bin).
        :param edges: The edges of the histograms, [xmin, xmax].
        :return: The figure and axes with the plot.
        """
        fig, axes = plt.subplots(figsize=(5, 5))
        if self.normalize:
            axes.set_xlabel(r"Distance from halo center [$R_{200c}$]")
        else:
            axes.set_xlabel("Distance from halo center [kpc]")
        axes.set_ylabel(r"Gas density [$M_\odot / kpc^3$]")
        if self.log:
            axes.set_yscale("log")

        xs = (edges[:-1] + edges[1:]) / 2

        # plot mass bins
        for i in range(len(stacks)):
            if i == len(stacks) - 1:
                color = "black"
                label = "Total"
            else:
                color = colormaps.sample_cmap("jet", len(stacks) - 1, i)
                label = (
                    rf"$10^{{{np.log10(self.mass_bins[i]):.1f}}} - "
                    rf"10^{{{np.log10(self.mass_bins[i + 1]):.1f}}}$"
                )

            # error config
            if self.method == "mean":
                total_errors = errors[i][0]
                cool_errors = errors[i][1]
            elif self.method == "median":
                total_errors = pltutil.get_errorbar_lengths(
                    stacks[i][0], [errors[i][0][0], errors[i][0][1]]
                )
                cool_errors = pltutil.get_errorbar_lengths(
                    stacks[i][1], [errors[i][1][0], errors[i][1][1]]
                )
            else:
                logging.fatal(f"Unrecognised plot method {self.method}.")
                sys.exit(4)

            # total as solid line
            common.plot_curve_with_error_region(
                xs,
                stacks[i][0],
                x_err=None,
                y_err=total_errors,
                axes=axes,
                linestyle="solid",
                color=color,
                label=label,
                suppress_error_line=True,
                suppress_error_region=False,
            )
            # plot cool gas only as dashed line
            common.plot_curve_with_error_region(
                xs,
                stacks[i][1],
                x_err=None,
                y_err=cool_errors,
                axes=axes,
                linestyle="dashed",
                color=color,
                suppress_error_line=True,
                suppress_error_region=False,
            )

        axes.legend(
            loc="lower center",
            bbox_to_anchor=(0.5, 1.1),
            ncol=len(stacks) // 2,
        )
        return fig, axes


@dataclass
class StackDensityProfilesCombinedPipeline(StackProfilesBinnedPipeline):
    """
    Pipeline to create stacks of radial profiles, binned by halo mass.

    Pipeline produces a plot with both mean and median shown in the
    same plot.

    Requires the data files for profiles of TNG300-1 and TNG Cluster to
    already exist, since they will be loaded.
    """

    def run(self) -> int:
        """
        Plot radial density profiles for different mass bins.

        This will load the existing profile data from both TNG300-1 and
        TNG Cluster and bin the histograms by halo mass. Then, a plot
        will be created, containing the mean AND median profile within
        each mass bin, plus a total profile, containing all clusters of
        both simulations (i.e. halos with log M > 14).

       The plot will be a single plot containing the mean lines for the
       total gas density and the cool density, as well as lines for the
       median cool gas density.

        Steps:

        1. Allocate space in memory for histogram data.
        2. Load halo data for TNG300-1
        3. Load halo data for TNG Cluster
        4. Create a mass bin mask.
        5. Allocate memory space for mean and median histograms per mass
           bin and a total over all mass bins (8 histograms)
        6. For every mass bin:
           1. Mask histogram data
           2. Calculate mean and median profile with errors
           3. Place data in allocated memory
        7. Create a total mean/median histogram with errors; place it
           into allocated memory.
        8. Plot the data according to chosen type.

        :return: Exit code.
        """
        # Step 0: verify directories
        self._verify_directories()

        # Step 1 - 3: Load data (depends on what type of data, so this
        # task is split):
        logging.info("Loading halo data from TNG300-1 and TNG Cluster.")
        cluster_data = self._load_density_hists()

        # unpack
        cluster_masses = cluster_data["masses"]
        cluster_histograms = cluster_data["histograms"]
        """Shape: (H, 2, N) for H = number of halos, N = number of bins"""
        edges = cluster_data["edges"]

        # Step 4: create mass bin mask
        logging.info("Creating a mass bin mask.")
        n_mass_bins = len(self.mass_bins) - 1
        mask = selection.digitize_clusters(cluster_masses, self.mass_bins)

        # Step 5: allocate memory for mean/median histograms
        stacks = np.zeros(
            (n_mass_bins + 1, 3, ) + cluster_histograms[0, 0].shape
        )  # yapf:disable
        """Axes: mass bin, total/mean cool/median cool, histogram bins"""
        errors = np.zeros(
            (n_mass_bins + 1, 3, 2, ) + cluster_histograms[0, 0].shape
        )  # yapf: disable
        """
        Axes: mass bin, total/mean cool/median cool, lower/upper error, values
        """

        # Step 6: loop over mass bins and create stacks
        logging.info("Start stacking histograms per mass bin.")
        # loop over mass bins
        for i in range(n_mass_bins):
            # mask histogram data
            hists_in_bin = selection.mask_quantity(
                cluster_histograms, mask, index=(i + 1)
            )
            stacks[i], errors[i] = self._stack_density_hists(hists_in_bin)

        # Step 7: create a total mean/median profile
        stacks[-1], errors[-1] = self._stack_density_hists(cluster_histograms)

        # Step 8: plot the data
        logging.info("Plotting combined density stacks.")
        f, _ = self._plot_density_stacks(stacks, errors, edges)
        logging.info("Saving combined radial density profile to file.")
        self._save_fig(f, ident_flag="combined")

        return 0

    def _stack_density_hists(
        self,
        histograms: NDArray,
    ) -> tuple[NDArray, NDArray]:
        """
        Stack the temperature histograms given according to self.method.

        The method expects ``histograms`` to be an array of shape
        (N, 2, X) where N is the number of clusters in the mass bin to
        stack, and X is the number of radial bins. The second axis is
        supposed to split the histograms into a total profile (index 0)
        and a cool-gas-only profile (index 1), i.e. ``histograms[i][0]``
        selects the total density profile of the i-th cluster, while
        ``histograms[i][1]`` selects the density profile for only the
        cool gas of the i-th halo.

        :param histograms: The array of histograms of shape (2, X) to
            stack. Along the first axis, the first entry must be the
            total gas density profile, the second entry must be the
            cool-gas-only density profile.
        :return: Tuple of the stacked histogram (shape (2, Y)) and the
            error (shape (2, 2, X)). For the error, the axes are
            assigned as (total/cool gas, lower/upper error, bins).
        """
        # splice input array [all halos, total/cool only, full histogram]
        total_hists = histograms[:, 0, :]
        cool_gas_hists = histograms[:, 1, :]
        # stack arrays separately
        total_stack, total_lowerr, total_upperr = statistics.stack_histograms(
            total_hists, "mean"
        )
        cool_mean, cool_lowstd, cool_uppstd = statistics.stack_histograms(
            cool_gas_hists, "mean"
        )
        cool_median, cool_lowerr, cool_uperr = statistics.stack_histograms(
            cool_gas_hists, "median"
        )
        # construct expected return array shape
        stack = np.array([total_stack, cool_mean, cool_median])
        errors = np.array(
            [
                [total_lowerr, total_upperr],
                [cool_lowstd, cool_uppstd],
                [cool_lowerr, cool_uperr],
            ]
        )
        return stack, errors

    def _plot_density_stacks(
        self, stacks: NDArray, errors: NDArray, edges: NDArray
    ) -> tuple[Figure, Axes]:
        """
        Plot the stacked density profiles.

        :param stacks: The array of N + 1 stacked density histograms,
            where N is the number of mass bins. Histograms are expected
            to be 2D of shape (2, X). The first axes splits the 1D
            histograms into total stack (first entry) and cool gas only
            stack (second entry). X is the number of radial bins.
        :param errors: The array of errors on the stacks, of shape
            (N + 1, 2, 2, X). This corresponds to the following quantities:
            (bin/total, total/cool-only, lower/upper error, bin).
        :param edges: The edges of the histograms, [xmin, xmax].
        :return: The figure and axes with the plot.
        """
        if self.core_only:
            width = 4.5
        else:
            width = 7
        fig, axes = plt.subplots(figsize=(width, 4.5))
        if self.normalize:
            axes.set_xlabel(r"Distance from halo center [$R_{200c}$]")
        else:
            axes.set_xlabel("Distance from halo center [kpc]")
        axes.set_ylabel(r"Gas density [$M_\odot / kpc^3$]")
        if self.core_only:
            axes.set_ylim((1e1, 1e8))
            axes.set_xlim((0, 0.05))
        else:
            axes.set_ylim((1e-2, 1e6))
            axes.set_xlim((0, 2))
        if self.log:
            axes.set_yscale("log")

        xs = (edges[:-1] + edges[1:]) / 2

        # plot mass bins
        for i in range(len(stacks)):
            if i == len(stacks) - 1:
                color = "black"
                label = "Total"
                linewidth = 1.8
            else:
                color = colormaps.sample_cmap("jet", len(stacks) - 1, i)
                label = (
                    rf"$10^{{{np.log10(self.mass_bins[i]):.1f}}} - "
                    rf"10^{{{np.log10(self.mass_bins[i + 1]):.1f}}} M_\odot$"
                )
                linewidth = None

            # error config
            total_errors = errors[i][0]
            cool_std = errors[i][1]
            cool_errors = pltutil.get_errorbar_lengths(
                stacks[i][2], [errors[i][2][0], errors[i][2][1]]
            )

            # total mean as solid line
            common.plot_curve_with_error_region(
                xs,
                stacks[i][0],
                x_err=None,
                y_err=total_errors,
                axes=axes,
                linestyle="solid",
                linewidth=linewidth,
                color=color,
                label=label,
                suppress_error_line=True,
                suppress_error_region=True,
            )
            # plot cool gas mean only as dashed line
            common.plot_curve_with_error_region(
                xs,
                stacks[i][1],
                x_err=None,
                y_err=cool_std,
                axes=axes,
                linestyle="dashed",
                linewidth=linewidth,
                color=color,
                suppress_error_line=True,
                suppress_error_region=True,
            )
            # plot cool gas median only as dotted line
            common.plot_curve_with_error_region(
                xs,
                stacks[i][2],
                x_err=None,
                y_err=cool_errors,
                axes=axes,
                linestyle="dotted",
                linewidth=linewidth,
                color=color,
                suppress_error_line=True,
                suppress_error_region=True,
            )

        # add to artists handles for linestyles (full range only)
        if not self.core_only:
            handles = [
                matplotlib.lines.Line2D(
                    [], [],
                    marker="none",
                    color="black",
                    ls="solid",
                    label="All gas"
                ),
                matplotlib.lines.Line2D(
                    [], [],
                    marker="none",
                    color="black",
                    ls="dashed",
                    label="Cool gas (mean)"
                ),
                matplotlib.lines.Line2D(
                    [], [],
                    marker="none",
                    color="black",
                    ls="dotted",
                    label="Cool gas (median)"
                ),
            ]
            first_legend = axes.legend(
                handles=handles,
                loc="upper left",
                bbox_to_anchor=(0.15, 1.),
                fontsize="small",
            )
            axes.add_artist(first_legend)

        axes.legend(
            loc="upper right",
            # bbox_to_anchor=(0.5, 1.1),
            ncol=2,
            prop={"size": 8},
        )
        return fig, axes


@dataclass
class StackDensityProfilesByVelocityPipeline(
        StackDensityProfilesCombinedPipeline):
    """
    Pipeline to create stacks of radial profiles, split by in-/outflow.

    Pipeline produces a plot with the mean split by radial velocity and
    binned into 0.2 dex mass bins.

    Requires the data files for profiles of TNG300-1 and TNG Cluster to
    already exist, since they will be loaded.
    """

    regime: Literal["cool", "warm", "hot"] = "cool"

    def run(self) -> int:
        """
        Create stacks of density profile split by velocity.

        Plots only the cool gas mean, split by in- and outflowing gas.

        Steps:

        1. Allocate space in memory for histogram data.
        2. Load halo data for TNG300-1
        3. Load halo data for TNG Cluster
        4. Create a mass bin mask.
        5. Allocate memory space for mean histograms per mass
           bin and a total over all mass bins (8 histograms)
        6. For every mass bin:
           1. Mask histogram data
           2. Calculate mean profile with errors
           3. Place data in allocated memory
        7. Create a total mean/median histogram with errors; place it
           into allocated memory.
        8. Plot the data according to chosen type.

        :return: Exit code.
        """
        # Step 0: verify directories
        self._verify_directories()

        # Step 1 - 3: Load data (depends on what type of data, so this
        # task is split):
        logging.info("Loading halo data from TNG300-1 and TNG Cluster.")
        cluster_data = self._load_density_hists()

        # unpack
        cluster_masses = cluster_data["masses"]
        # Shape: (H, 2, N) for H = number of halos, N = number of radial bins:
        cluster_histograms = cluster_data["histograms"]
        edges = cluster_data["edges"]

        # Step 4: create mass bin mask
        logging.info("Creating a mass bin mask.")
        n_mass_bins = len(self.mass_bins) - 1
        mask = selection.digitize_clusters(cluster_masses, self.mass_bins)

        # Step 5: allocate memory for mean/median histograms
        stacks = np.zeros(
            (n_mass_bins + 1, 3, ) + cluster_histograms[0, 0].shape
        )  # yapf: disable
        """Axes: mass bin, cool inflow/outflow/total, histogram bins"""
        errors = np.zeros(
            (n_mass_bins + 1, 3, 2,) + cluster_histograms[0, 0].shape
        )  # yapf: disable
        """
        Axes: mass bin, total/mean cool/median cool, lower/upper error, values
        """

        # Step 6: loop over mass bins and create stacks
        logging.info("Start stacking histograms per mass bin.")
        # loop over mass bins
        for i in range(n_mass_bins):
            # mask histogram data
            hists_in_bin = selection.mask_quantity(
                cluster_histograms, mask, index=(i + 1)
            )
            stacks[i], errors[i] = self._stack_density_hists(hists_in_bin)

        # Step 7: create a total mean/median profile
        stacks[-1], errors[-1] = self._stack_density_hists(cluster_histograms)

        # Step 8: plot the data
        logging.info("Plotting combined density stacks.")
        f, _ = self._plot_density_stacks(stacks, errors, edges)
        logging.info("Saving combined radial density profile to file.")
        self._save_fig(f, ident_flag=f"{self.regime}_gas_split_by_flow")

        return 0

    def _load_density_hists(self) -> dict[str, NDArray]:
        """
        Load density histograms, edges and halo masses.

        The returned values are a concatenation of TNG300-1 and TNG
        Cluster data, in that order (meaning the first 280 entries of
        the data arrays of the 'masses' and 'histograms' fields belong
        to TNG300-1, the remaining 352 belong to TNG Cluster).

        .. attention::
            The field 'histograms' perhaps unexpectedly has shape (2, N).
            This is due to the fact that the first entry of this array
            is the inflow histogram for cool gas and the second entry is
            the outflow histogram of the cool gas only!

        :return: Mapping of loaded data. Has as keys 'masses', 'histograms',
            and 'edges', with the values representing the cluster masses,
            the density histograms and the edges of the histograms for
            all clusters of TNG300-1 and TNG Cluster.
        """
        # determine shape and edges
        test_path = (
            self.paths["data_dir"] / "TNG300_1"
            / f"density_profiles{self.suffix}"
        )
        filepath = list(test_path.iterdir())[0]
        with np.load(filepath.resolve()) as test_file:
            shape = test_file["total_inflow"].shape
            edges = np.array(test_file["edges"])

        # allocate memory
        masses = np.zeros(self.n_clusters)
        hists = np.zeros((self.n_clusters, 2,) + shape)  # yapf: disable

        # load TNG300-1 data
        load_generator = load_radial_profiles.load_individuals_1d_profile(
            self.paths["data_dir"] / "TNG300_1"
            / f"density_profiles{self.suffix}",
            shape,  # automatically validates shapes
        )
        n_tng300_clusters = 0
        for i, halo_data in enumerate(load_generator):
            masses[i] = halo_data["halo_mass"]
            hists[i][0] = halo_data[f"{self.regime}_inflow"]
            hists[i][1] = halo_data[f"{self.regime}_outflow"]
            n_tng300_clusters += 1

        # load TNG Cluster data and verify it
        load_generator = load_radial_profiles.load_individuals_1d_profile(
            self.paths["data_dir"] / "TNG_Cluster"
            / f"density_profiles{self.suffix}",
            shape,  # automatically validates shapes
        )
        for i, halo_data in enumerate(load_generator):
            if i == 0:
                if not np.allclose(halo_data["edges"], edges):
                    logging.fatal(
                        f"Density histograms for TNG300-1 and TNG-Cluster "
                        f"have different bin edges:\nTNG300: {edges}\n"
                        f"TNG-Cluster: {halo_data['edges']}"
                    )
                    sys.exit(2)
            masses[i + n_tng300_clusters] = halo_data["halo_mass"]
            hists[i + n_tng300_clusters][0] = (
                halo_data[f"{self.regime}_inflow"]
            )
            hists[i + n_tng300_clusters][1] = (
                halo_data[f"{self.regime}_outflow"]
            )

        # construct and return mapping
        return {"masses": masses, "histograms": hists, "edges": edges}

    def _stack_density_hists(
        self,
        histograms: NDArray,
    ) -> tuple[NDArray, NDArray]:
        """
        Return the mean of the density histograms for in- and outflow.

        :param histograms: The array of histograms of shape (H, 2, X) to
            stack. Along the first axis, the first entry must be the
            cool gas density profile of inflowing gas, the second entry
            must be the cool gas density profile for outflowing gas.
        :return: Tuple of the stacked histogram (shape (2, Y)) and the
            error (shape (2, 2, X)). For the error, the axes are
            assigned as (total/cool gas, lower/upper error, bins).
        """
        # splice input array [all halos, inflow/outflow, full histogram]
        inflow = histograms[:, 0, :]
        outflow = histograms[:, 1, :]
        # stack arrays separately
        total_stack, total_std, _ = statistics.stack_histograms(
            inflow + outflow, "mean"
        )
        inflow_mean, inflow_std, _ = statistics.stack_histograms(
            inflow, "mean"
        )
        outflow_mean, outflow_std, _ = statistics.stack_histograms(
            outflow, "mean"
        )
        # construct expected return array shape
        stack = np.array([total_stack, inflow_mean, outflow_mean])
        errors = np.array(
            [
                [total_std, total_std],
                [inflow_std, inflow_std],
                [outflow_std, outflow_std],
            ]
        )
        return stack, errors

    def _plot_density_stacks(
        self, stacks: NDArray, errors: NDArray, edges: NDArray
    ) -> tuple[Figure, Axes]:
        """
        Plot the density profiles, split by velocity.

        :param stacks: The stacks of the density profile, split into
            mass bins. Shape (N + 1, 3, B) where N is the number of
            mass bins (last entry is the total) and B is the number of
            radial bins.
        :param errors: The errors on the density profiles, of shape
            (N + 1, 3, 2, B) where the third axis holds lower and
            upper error respectively.
        :param edges: The edges of the radial bins, shape (B + 1, ).
        :return: Tuple of figure and axes.
        """
        if self.core_only:
            figsize = (5, 4)
        else:
            figsize = (8, 4)
        fig, axes = plt.subplots(
            figsize=figsize,
            ncols=2,
            sharey=True,
            gridspec_kw={"hspace": 0, "wspace": 0},
        )
        fig.set_tight_layout(True)
        if self.normalize:
            xlabel = r"Distance from halo center [$R_{200c}$]"
        else:
            xlabel = "Distance from halo center [kpc]"
        axes[0].set_xlabel(xlabel)
        axes[1].set_xlabel(xlabel)
        axes[0].set_ylabel(r"Gas density [$M_\odot / kpc^3$]")

        if self.core_only:
            ylim = (1e1, 1e7)
        else:
            if self.regime == "hot":
                ylim = (5, 1e6)
            else:
                ylim = (1e-2, 1e5)
        axes[0].set_ylim(ylim)
        axes[1].set_ylim(ylim)
        if self.log:
            axes[0].set_yscale("log")
            axes[1].set_yscale("log")

        # x-data
        xs = (edges[:-1] + edges[1:]) / 2

        # left plot: total over all mass bins, with error
        common.plot_curve_with_error_region(
            xs,
            stacks[-1][0],
            None,
            errors[-1][0],
            axes[0],
            linestyle="solid",
            label="Both",
            suppress_error_region=False,
            suppress_error_line=True,
        )
        common.plot_curve_with_error_region(
            xs,
            stacks[-1][1],
            None,
            errors[-1][1],
            axes[0],
            linestyle="solid",
            label="Inflowing",
            color="dodgerblue",
            suppress_error_region=False,
            suppress_error_line=True,
        )
        common.plot_curve_with_error_region(
            xs,
            stacks[-1][2],
            None,
            errors[-1][2],
            axes[0],
            linestyle="solid",
            label="Outflowing",
            color="crimson",
            suppress_error_region=False,
            suppress_error_line=True,
        )

        # right plot: gas flows in different mass bins
        for i in range(len(stacks)):
            if i == len(stacks) - 1:
                color = "black"
                label = "Total"
            else:
                color = colormaps.sample_cmap("jet", len(stacks) - 1, i)
                label = (
                    rf"$10^{{{np.log10(self.mass_bins[i]):.1f}}} - "
                    rf"10^{{{np.log10(self.mass_bins[i + 1]):.1f}}} M_\odot$"
                )

            # inflowing mean
            common.plot_curve_with_error_region(
                xs,
                stacks[i][1],
                x_err=None,
                y_err=errors[i][1],
                axes=axes[1],
                linestyle="solid",
                color=color,
                label=label,
                suppress_error_line=True,
                suppress_error_region=True,
            )
            # outflowing mean
            common.plot_curve_with_error_region(
                xs,
                stacks[i][2],
                x_err=None,
                y_err=errors[i][2],
                axes=axes[1],
                linestyle="dashed",
                color=color,
                suppress_error_line=True,
                suppress_error_region=True,
            )

        axes[0].legend(loc="upper right", prop={"size": 8})
        axes[1].legend(
            loc="upper right",
            # bbox_to_anchor=(0.5, 1.1),
            ncol=2,
            prop={"size": 8},
        )
        return fig, axes
