import os
import pickle

import numpy as np
import pandas as pd
from experiment_setup import (
    get_default_config,
    setup_fast_experiments,
)
from interfaces import select_model

from GravNN.Networks.Configs import *
from GravNN.Networks.Data import DataSet
from GravNN.Networks.utils import populate_config_objects
from GravNN.Visualization.ExtrapolationVisualizer import ExtrapolationVisualizer


def get_planes_metrics(exp):
    percent_error = np.nanmean(exp.percent_error_acc)
    rms_error = np.nanmean(exp.RMS_acc)
    return {
        "percent_planes": percent_error,
        "rms_planes": rms_error,
    }


def get_extrap_metrics(exp):
    # Interior, Exterior, Extrapolation
    vis = ExtrapolationVisualizer(exp, x_axis="dist_2_COM")

    x = vis.x_test
    x_interpolation = x[: vis.max_idx]
    interior_mask = x_interpolation < 1.0

    y_interpolation = (
        vis.experiment.losses["percent"][vis.idx_test][: vis.max_idx] * 100
    )
    y_extrapolation = (
        vis.experiment.losses["percent"][vis.idx_test][vis.max_idx :] * 100
    )

    y_interior = y_interpolation[interior_mask]
    y_exterior = y_interpolation[~interior_mask]

    percent_interior = np.nanmean(y_interior)
    percent_exterior = np.nanmean(y_exterior)
    percent_extrapolation = np.nanmean(y_extrapolation)

    metrics = {
        "percent_interior": percent_interior,
        "percent_exterior": percent_exterior,
        "percent_extrapolation": percent_extrapolation,
    }
    return metrics


def get_traj_metrics(exp):
    test_model = exp.test_models[0]
    pos_error = test_model.metrics["pos_diff"][-1]
    state_error = test_model.metrics["state_diff"][-1]
    dt = test_model.orbit.elapsed_time[-1]

    return {
        "pos_error": pos_error,
        "state_error": state_error,
        "dt": dt,
    }


def get_surface_metrics(exp):
    surface_error = np.mean(exp.percent_error_acc)
    return {
        "percent_surface": surface_error,
    }


def get_training_metrics(model):
    filename = model.filename
    time_file = os.path.splitext(filename)[0] + "_time.data"
    with open(str(time_file), "rb") as f:
        train_duration = pickle.load(f)
    return {
        "train_duration": train_duration,
    }


def extract_metrics(model):
    metrics = {}
    metrics.update(get_planes_metrics(model.plane_exp))
    metrics.update(get_extrap_metrics(model.extrap_exp))
    metrics.update(get_traj_metrics(model.trajectory_exp))
    metrics.update(get_surface_metrics(model.surface_exp))
    metrics.update(get_training_metrics(model))
    return metrics


def load_experiment(experiment, config):
    model = select_model(experiment["model_name"][0])

    # Necessary to get scaling
    _ = DataSet(config)

    model.configure(config)
    model.load()
    model.evaluate()
    return model


def main():
    # experiments = setup_experiments()
    experiments = setup_fast_experiments()

    metrics_list = []
    for idx, exp in enumerate(experiments):
        model_name = exp["model_name"][0]
        config = get_default_config(model_name)
        config.update(exp)
        config = populate_config_objects(config)
        config["comparison_idx"] = [idx]

        model = load_experiment(exp, config)
        metrics = extract_metrics(model)
        metrics.update({"model_name": model_name})
        metrics_list.append(metrics)

    df = pd.DataFrame(metrics_list)
    # save dataframe for interactive analysis
    df.to_pickle("Data/Dataframes/comparison_metrics.data")


if __name__ == "__main__":
    main()
