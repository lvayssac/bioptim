"""
TODO: Cleaning
This is a basic example on how to use muscle driven to perform an optimal reaching task.
The arm must reach a marker while minimizing the muscles activity and the states. The problem is solved using both
ACADOS and Ipopt.
"""


import biorbd_casadi as biorbd
from time import time
import numpy as np
from bioptim import (
    OptimalControlProgram,
    ObjectiveList,
    ObjectiveFcn,
    DynamicsList,
    DynamicsFcn,
    BoundsList,
    QAndQDotBounds,
    InitialGuessList,
    InitialGuess,
    Solver,
    InterpolationType,
)


def prepare_ocp(biorbd_model_path, final_time, n_shooting, x_warm=None, use_sx=False, n_threads=1):
    # --- Options --- #
    # Model path
    biorbd_model = biorbd.Model(biorbd_model_path)
    tau_min, tau_max, tau_init = -50, 50, 0
    muscle_min, muscle_max, muscle_init = 0, 1, 0.5

    # Add objective functions
    objective_functions = ObjectiveList()
    objective_functions.add(ObjectiveFcn.Lagrange.MINIMIZE_STATE, key="q", weight=10, multi_thread=False)
    objective_functions.add(ObjectiveFcn.Lagrange.MINIMIZE_STATE, key="qdot", weight=10, multi_thread=False)
    objective_functions.add(ObjectiveFcn.Lagrange.MINIMIZE_CONTROL, key="tau", weight=10, multi_thread=False)
    objective_functions.add(ObjectiveFcn.Lagrange.MINIMIZE_CONTROL, key="muscles", weight=10, multi_thread=False)
    objective_functions.add(
        ObjectiveFcn.Mayer.SUPERIMPOSE_MARKERS, weight=100000, first_marker="target", second_marker="COM_hand"
    )

    # Dynamics
    dynamics = DynamicsList()
    dynamics.add(DynamicsFcn.MUSCLE_DRIVEN, with_residual_torque=True)

    # Path constraint
    x_bounds = BoundsList()
    x_bounds.add(bounds=QAndQDotBounds(biorbd_model))
    x_bounds[0][:, 0] = (1.0, 1.0, 0, 0)

    # Initial guess
    if x_warm is None:
        x_init = InitialGuess([1.57] * biorbd_model.nbQ() + [0] * biorbd_model.nbQdot())
    else:
        x_init = InitialGuess(x_warm, interpolation=InterpolationType.EACH_FRAME)

    # Define control path constraint
    u_bounds = BoundsList()
    u_bounds.add(
        [tau_min] * biorbd_model.nbGeneralizedTorque() + [muscle_min] * biorbd_model.nbMuscleTotal(),
        [tau_max] * biorbd_model.nbGeneralizedTorque() + [muscle_max] * biorbd_model.nbMuscleTotal(),
    )

    u_init = InitialGuessList()
    u_init.add([tau_init] * biorbd_model.nbGeneralizedTorque() + [muscle_init] * biorbd_model.nbMuscleTotal())
    # ------------- #

    return OptimalControlProgram(
        biorbd_model,
        dynamics,
        n_shooting,
        final_time,
        x_init,
        u_init,
        x_bounds,
        u_bounds,
        objective_functions,
        use_sx=use_sx,
        n_threads=n_threads,
    )


def main():
    # Options
    warm_start_ipopt_from_acados_solution = False

    # --- Solve the program using ACADOS --- #
    ocp_acados = prepare_ocp(biorbd_model_path="arm26.bioMod", final_time=2, n_shooting=51, use_sx=True)

    tic = time()
    sol_acados = ocp_acados.solve(
        solver=Solver.ACADOS,
        show_online_optim=False,
        solver_options={
            "nlp_solver_tol_comp": 1e-3,
            "nlp_solver_tol_eq": 1e-3,
            "nlp_solver_tol_stat": 1e-3,
        },
    )
    toc_acados = time() - tic

    # --- Solve the program using IPOPT --- #
    x_warm = sol_acados["qqdot"] if warm_start_ipopt_from_acados_solution else None
    ocp_ipopt = prepare_ocp(
        biorbd_model_path="arm26.bioMod",
        final_time=2,
        x_warm=x_warm,
        n_shooting=51,
        use_sx=False,
        n_threads=6,
    )

    tic = time()
    sol_ipopt = ocp_ipopt.solve(
        solver=Solver.IPOPT,
        show_online_optim=False,
        solver_options={
            "tol": 1e-3,
            "dual_inf_tol": 1e-3,
            "constr_viol_tol": 1e-3,
            "compl_inf_tol": 1e-3,
            "linear_solver": "ma57",
            "max_iter": 100,
            "hessian_approximation": "exact",
        },
    )
    toc_ipopt = time() - tic

    # --- Show results --- #
    print("\n\n")
    print("Results using ACADOS")
    print(f"Final objective: {np.nansum(sol_acados.cost)}")
    sol_acados.print()
    print(f"Time to solve: {sol_acados.time_to_optimize}sec")
    print(f"")

    print(
        f"Results using Ipopt{'' if warm_start_ipopt_from_acados_solution else ' not'} "
        f"warm started from ACADOS solution"
    )
    print(f"Final objective : {np.nansum(sol_ipopt.cost)}")
    sol_ipopt.print()
    print(f"Time to solve: {sol_ipopt.time_to_optimize}sec")
    print(f"")

    visualizer = sol_acados.animate(show_now=False)
    visualizer.extend(sol_ipopt.animate(show_now=False))

    # Update biorbd-viz by hand so they can be visualized simultaneously
    should_continue = True
    while should_continue:
        for i, b in enumerate(visualizer):
            if b.vtk_window.is_active:
                b.update()
            else:
                should_continue = False


if __name__ == "__main__":
    main()
