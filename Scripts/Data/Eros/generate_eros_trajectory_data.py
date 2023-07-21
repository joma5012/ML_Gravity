import os

from GravNN.CelestialBodies.Asteroids import Eros
from GravNN.GravityModels.HeterogeneousPoly import generate_heterogeneous_sym_model
from GravNN.Trajectories.utils import (
    generate_near_hopper_trajectories,
    generate_near_orbit_trajectories,
)


def main():
    num_nodes = int(os.environ["SLURM_JOB_NUM_NODES"])
    cores_per_nodes = int(os.environ["SLURM_JOB_CPUS_PER_NODE"])
    num_threads = os.cpu_count()

    print(f"Cores Per Node:{os.environ['SLURM_JOB_CPUS_PER_NODE']}")
    print(f"Num Nodes:{os.environ['SLURM_JOB_NUM_NODES']}")
    print(f"Available Cores:{num_nodes * cores_per_nodes}")
    print(f"Threads per core:{num_threads / (cores_per_nodes * num_nodes)}")

    # print(f"Num Threads:{mp.cpu_count()}")
    planet = Eros()
    model_file = planet.obj_8k

    trajectories = generate_near_orbit_trajectories(sampling_inteval=60 * 10)
    for trajectory in trajectories:
        generate_heterogeneous_sym_model(
            planet,
            model_file,
            trajecvtory=trajectory,
        ).load()

    trajectories = generate_near_hopper_trajectories(sampling_inteval=60 * 10)
    for trajectory in trajectories:
        generate_heterogeneous_sym_model(
            planet,
            model_file,
            trajecvtory=trajectory,
        ).load()


if __name__ == "__main__":
    main()
