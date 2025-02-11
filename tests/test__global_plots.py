"""
Test for file IO
"""
import io
import sys
import os
import pytest

from casadi import Function, MX

import numpy as np
import biorbd_casadi as biorbd
from bioptim import OptimalControlProgram

from .utils import TestUtils

import matplotlib

matplotlib.use("Agg")


def test_plot_graphs_one_phase():
    # Load graphs_one_phase
    bioptim_folder = TestUtils.bioptim_folder()
    graph = TestUtils.load_module(bioptim_folder + "/examples/torque_driven_ocp/track_markers_with_torque_actuators.py")

    ocp = graph.prepare_ocp(
        biorbd_model_path=bioptim_folder + "/examples/torque_driven_ocp/cube.bioMod",
        n_shooting=30,
        final_time=2,
    )
    sol = ocp.solve()
    sol.graphs(automatically_organize=False)


def test_plot_merged_graphs():
    # Load graphs_one_phase
    bioptim_folder = TestUtils.bioptim_folder()
    merged_graphs = TestUtils.load_module(bioptim_folder + "/examples/muscle_driven_ocp/muscle_excitations_tracker.py")

    # Define the problem
    model_path = bioptim_folder + "/examples/muscle_driven_ocp/arm26.bioMod"
    biorbd_model = biorbd.Model(model_path)
    final_time = 0.5
    n_shooting = 9

    # Generate random data to fit
    np.random.seed(42)
    t, markers_ref, x_ref, muscle_excitations_ref = merged_graphs.generate_data(biorbd_model, final_time, n_shooting)

    biorbd_model = biorbd.Model(model_path)  # To prevent from non free variable, the model must be reloaded
    ocp = merged_graphs.prepare_ocp(
        biorbd_model,
        final_time,
        n_shooting,
        markers_ref,
        muscle_excitations_ref,
        x_ref[: biorbd_model.nbQ(), :].T,
        use_residual_torque=True,
        kin_data_to_track="markers",
    )
    sol = ocp.solve()
    sol.graphs(automatically_organize=False)


def test_plot_graphs_multi_phases():
    # Load graphs_one_phase
    bioptim_folder = TestUtils.bioptim_folder()
    graphs = TestUtils.load_module(bioptim_folder + "/examples/getting_started/example_multiphase.py")
    ocp = graphs.prepare_ocp(biorbd_model_path=bioptim_folder + "/examples/getting_started/cube.bioMod")
    sol = ocp.solve()
    sol.graphs(automatically_organize=False)


def test_add_new_plot():
    # Load graphs_one_phase
    bioptim_folder = TestUtils.bioptim_folder()
    graphs = TestUtils.load_module(
        bioptim_folder + "/examples/torque_driven_ocp/track_markers_with_torque_actuators.py"
    )
    ocp = graphs.prepare_ocp(
        biorbd_model_path=bioptim_folder + "/examples/torque_driven_ocp/cube.bioMod",
        n_shooting=20,
        final_time=0.5,
    )
    sol = ocp.solve(solver_options={"max_iter": 1})

    # Saving/loading files reset the plot settings to normal
    save_name = "test_plot.bo"
    ocp.save(sol, save_name)

    # Test 1 - Working plot
    ocp.add_plot("My New Plot", lambda x, u, p: x[0:2, :])
    sol.graphs(automatically_organize=False)

    # Test 2 - Combine using combine_to is not allowed
    ocp, sol = OptimalControlProgram.load(save_name)
    with pytest.raises(RuntimeError):
        ocp.add_plot("My New Plot", lambda x, u, p: x[0:2, :], combine_to="NotAllowed")

    # Test 3 - Create a completely new plot
    ocp, sol = OptimalControlProgram.load(save_name)
    ocp.add_plot("My New Plot", lambda x, u, p: x[0:2, :])
    ocp.add_plot("My Second New Plot", lambda x, p, u: x[0:2, :])
    sol.graphs(automatically_organize=False)

    # Test 4 - Combine to the first using fig_name
    ocp, sol = OptimalControlProgram.load(save_name)
    ocp.add_plot("My New Plot", lambda x, u, p: x[0:2, :])
    ocp.add_plot("My New Plot", lambda x, u, p: x[0:2, :])
    sol.graphs(automatically_organize=False)

    # Delete the saved file
    os.remove(save_name)


def test_console_objective_functions():
    # Load graphs_one_phase
    bioptim_folder = TestUtils.bioptim_folder()
    graphs = TestUtils.load_module(bioptim_folder + "/examples/getting_started/example_multiphase.py")
    ocp = graphs.prepare_ocp(biorbd_model_path=bioptim_folder + "/examples/getting_started/cube.bioMod")
    sol = ocp.solve()
    ocp = sol.ocp  # We will override ocp with known and controlled values for the test

    sol.constraints = np.array([range(sol.constraints.shape[0])]).T / 10
    # Create some consistent answer
    sol.time_to_optimize = 1.2345
    sol.real_time_to_optimize = 5.4321

    def override_penalty(pen):
        for cmp, p in enumerate(pen):
            if p:
                name = p.name.replace("->", "_").replace(" ", "_")
                x = MX.sym("x", *p.weighted_function.sparsity_in("i0").shape)
                u = MX.sym("u", *p.weighted_function.sparsity_in("i1").shape)
                param = MX.sym("param", *p.weighted_function.sparsity_in("i2").shape)
                weight = MX.sym("weight", *p.weighted_function.sparsity_in("i3").shape)
                target = MX.sym("target", *p.weighted_function.sparsity_in("i4").shape)
                dt = MX.sym("dt", *p.weighted_function.sparsity_in("i5").shape)

                p.function = Function(name, [x, u, param], [np.array([range(cmp, len(p.rows) + cmp)]).T])
                p.weighted_function = Function(
                    name, [x, u, param, weight, target, dt], [np.array([range(cmp + 1, len(p.rows) + cmp + 1)]).T]
                )

    override_penalty(ocp.g_internal)  # Override constraints in the ocp
    override_penalty(ocp.g)  # Override constraints in the ocp
    override_penalty(ocp.J_internal)  # Override objectives in the ocp
    override_penalty(ocp.J)  # Override objectives in the ocp

    for nlp in ocp.nlp:
        override_penalty(nlp.g_internal)  # Override constraints in the nlp
        override_penalty(nlp.g)  # Override constraints in the nlp
        override_penalty(nlp.J_internal)  # Override objectives in the nlp
        override_penalty(nlp.J)  # Override objectives in the nlp

    captured_output = io.StringIO()  # Create StringIO object
    sys.stdout = captured_output  # and redirect stdout.
    sol.print()
    expected_output = (
        "Solving time: 1.2345 sec\n"
        "Elapsed time: 5.4321 sec\n"
        "\n"
        "---- COST FUNCTION VALUES ----\n"
        "PHASE 0\n"
        "MINIMIZE_CONTROL:  3.00 (weighted 6.0)\n"
        "\n"
        "PHASE 1\n"
        "MINIMIZE_CONTROL:  3.00 (weighted 6.0)\n"
        "minimize_difference:  6.00 (weighted 9.0)\n"
        "\n"
        "PHASE 2\n"
        "MINIMIZE_CONTROL:  3.00 (weighted 6.0)\n"
        "\n"
        "Sum cost functions: 27.0\n"
        "------------------------------\n"
        "\n"
        "--------- CONSTRAINTS ---------\n"
        "PHASE 0\n"
        "CONTINUITY: 21.0\n"
        "PHASE_TRANSITION 0->1: 27.0\n"
        "SUPERIMPOSE_MARKERS: 6.0\n"
        "SUPERIMPOSE_MARKERS: 9.0\n"
        "\n"
        "PHASE 1\n"
        "CONTINUITY: 21.0\n"
        "PHASE_TRANSITION 1->2: 27.0\n"
        "SUPERIMPOSE_MARKERS: 6.0\n"
        "\n"
        "PHASE 2\n"
        "CONTINUITY: 21.0\n"
        "SUPERIMPOSE_MARKERS: 6.0\n"
        "\n"
        "------------------------------\n"
    )

    sys.stdout = sys.__stdout__  # Reset redirect.
    assert captured_output.getvalue() == expected_output
