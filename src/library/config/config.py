import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Config:
    """
    Hold a runtime configuration with information about the used simulation.

    Instances of this class can be used to easily run the same code
    with different configurations, i.e. with different parameters. For
    example, one can use two different Config instances with different
    base paths to test code with a lower resolution simulation and to
    run actual analysis with higher resolution data. In such a scenario,
    depending on which config is desired, the client can pass different
    Config instances.

    :param sim_name: name of the simulation to use, must match the simulation
        under the given ``base_path``
    :param base_path: the base path of the simulation to use
    :param snap_num: number of the snapshot to use
    :param mass_field: the field name to use as mass indicator for
        groups/halos
    :param radius_field: the field name to use as radius indicator for
        groups/halos
    """
    sim_name: str
    base_path: str
    snap_num: int
    mass_field: str
    radius_field: str
    data_home: str | Path
    figures_home: str | Path
    cool_gas_history: str | Path | None

    def __post_init__(self):
        """
        Set up aux fields from existing fields.
        """
        self.sim_path: str = self.sim_name.replace("-", "_")


class InvalidConfigPathError(Exception):
    """Raise when a loaded config contains invalid paths"""

    def __init__(self, path: Path, *args: object) -> None:
        super().__init__(*args)
        if not isinstance(path, Path):
            path = Path(path)
        self.path = path

    def __str__(self) -> str:
        if not self.path.exists():
            return f"The config path {self.path} does not exist"
        elif not self.path.is_dir():
            return f"The config path {self.path} does not point to a directory"
        else:
            return f"The config path {self.path} is not a valid config path"


class InvalidSimulationNameError(KeyError):
    """Raised when an unknown simulation name is used"""

    def __init__(self, name: str, loc: str, *args: Sequence[Any]) -> None:
        msg = (
            f"There is no entry for a simulation named {name} in the "
            f"config.yaml configuration file for {loc}."
        )
        super().__init__(msg, *args)


class MissingConfigFileError(FileNotFoundError):
    """Raised when the config file does not exist"""

    def __init__(self, *args: Sequence[Any]) -> None:
        msg = (
            "No config file for the project exists. Create a config file "
            "by running the `install.py` script at the project root."
        )
        super().__init__(msg, *args)


def get_default_config(
    sim: str,
    snap: int = 99,
    mass_field: str = "Group_M_Crit200",
    radius_field: str = "Group_R_Crit200"
) -> Config:
    """
    Return a configuration for the specified simulation.

    :param sim: name of the simulation as used in the simulation file
        directory, e.g. TNG50-3
    :param snap: snapshot number to use, defaults to 99 (z = 0)
    :param mass_field: name of the simulation field that signifies the
        halo mass, defaults to M_crit200
    :param radius_field: name of the simulation field that signifies the
        halo radius, defaults to R_crit200
    :return: configuration for this specific
    """
    # find directories for data and figures
    cur_dir = Path(__file__).parent.resolve()
    root_dir = cur_dir.parents[2]
    if not (root_dir / "config.yaml").exists():
        raise MissingConfigFileError()
    with open(root_dir / "config.yaml", "r") as config_file:
        stream = config_file.read()
    config = yaml.full_load(stream)

    # set paths
    figures_home = config["paths"]["figures_home"]
    if figures_home == "default":
        figures_home = root_dir / "figures"
    elif Path(figures_home).is_absolute():
        figures_home = Path(figures_home).resolve()
    else:
        figures_home = root_dir / figures_home

    data_home = config["paths"]["data_home"]
    if data_home == "default":
        data_home = root_dir / "data"
    elif Path(data_home).is_absolute():
        data_home = Path(data_home).resolve()
    else:
        data_home = root_dir / data_home

    try:
        base_path = Path(config["paths"]["base_paths"][sim]).resolve()
    except KeyError:
        raise InvalidSimulationNameError(sim, "base paths")

    # verify paths
    for path in [figures_home, data_home, base_path]:
        if not path.exists() or not path.is_dir():
            raise InvalidConfigPathError(path)

    # set file paths
    try:
        gas_data_file = config["paths"]["cool_gas_history_archive"][sim]
    except KeyError:
        gas_data_file = None
    else:
        if gas_data_file == "default":
            gas_data_file = (
                data_home / "tracer_history" / sim.replace("-", "_")
                / "cool_gas_history.hdf5"
            )
        elif Path(gas_data_file).is_absolute():
            gas_data_file = Path(gas_data_file).resolve()
        else:
            gas_data_file = data_home / gas_data_file

    # return config
    final_config = Config(
        sim,
        str(base_path),  # illustris_python does not support Path-likes
        snap_num=snap,
        mass_field=mass_field,
        radius_field=radius_field,
        data_home=data_home,
        figures_home=figures_home,
        cool_gas_history=gas_data_file,
    )
    return final_config


def get_supported_simulations() -> list[str]:
    """Return a list of the names of supported simulations."""
    # find directories for data and figures
    cur_dir = Path(__file__).parent.resolve()
    root_dir = cur_dir.parents[2]
    with open(root_dir / "config.yaml", "r") as config_file:
        stream = config_file.read()
    config = yaml.full_load(stream)
    return list(config["paths"]["base_paths"].keys())


def get_simulation_base_path(sim: str) -> str:
    """
    Return the base path of the given simulation as specified in config.

    :param sim: Name of the sim as given in the config.yaml.
    :raises InvalidSimulationNameError: When the given simulation name
        is not known/not present in the config.
    :return: The path to the base path of the simulation as string,
        fully resolved.
    """
    # find directories for data and figures
    cur_dir = Path(__file__).parent.resolve()
    root_dir = cur_dir.parents[2]
    with open(root_dir / "config.yaml", "r") as config_file:
        stream = config_file.read()
    config = yaml.full_load(stream)
    try:
        base_path = config["paths"]["base_paths"][sim]
    except KeyError:
        raise InvalidSimulationNameError(sim, "base paths")
    # resolve path and return it
    full_path = str(Path(base_path).resolve())
    logging.debug(f"Returning path to simulation {sim}: {full_path}")
    return full_path
