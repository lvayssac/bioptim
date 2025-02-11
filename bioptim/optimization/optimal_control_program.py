from typing import Union, Callable, Any
import os
import pickle
from copy import deepcopy
from math import inf

import biorbd_casadi as biorbd
import casadi
from casadi import MX, SX

from .non_linear_program import NonLinearProgram as NLP
from .optimization_vector import OptimizationVector
from ..dynamics.configure_problem import DynamicsList, Dynamics
from ..dynamics.ode_solver import OdeSolver, OdeSolverBase
from ..dynamics.configure_problem import ConfigureProblem
from ..gui.plot import CustomPlot, PlotOcp
from ..gui.graph import OcpToConsole, OcpToGraph
from ..interfaces.biorbd_interface import BiorbdInterface
from ..limits.constraints import ConstraintFunction, ConstraintFcn, ConstraintList, Constraint, ContinuityFunctions
from ..limits.phase_transition import PhaseTransitionList
from ..limits.objective_functions import ObjectiveFcn, ObjectiveList, Objective
from ..limits.path_conditions import BoundsList, Bounds
from ..limits.path_conditions import InitialGuess, InitialGuessList
from ..limits.path_conditions import InterpolationType
from ..limits.penalty import PenaltyOption
from ..limits.objective_functions import ObjectiveFunction
from ..misc.__version__ import __version__
from ..misc.enums import ControlType, Solver, Shooting
from ..misc.mapping import BiMappingList, Mapping
from ..misc.utils import check_version
from ..optimization.parameters import ParameterList, Parameter
from ..optimization.solution import Solution

check_version(biorbd, "1.6.1", "1.7.0")


class OptimalControlProgram:
    """
    The main class to define an ocp. This class prepares the full program and gives all
    the needed interface to modify and solve the program

    Attributes
    ----------
    cx: [MX, SX]
        The base type for the symbolic casadi variables
    g: list
        Constraints that are not phase dependent (mostly parameters and continuity constraints)
    g_internal: list[list[Constraint]]
        All the constraints internally defined by the OCP at each of the node of the phase
    J: list
        Objective values that are not phase dependent (mostly parameters)
    isdef_x_init: bool
        If the initial condition of the states are set
    isdef_x_bounds: bool
        If the bounds of the states are set
    isdef_u_init: bool
        If the initial condition of the controls are set
    isdef_u_bounds: bool
        If the bounds of the controls are set
    nlp: NLP
        All the phases of the ocp
    n_phases: Union[int, list, tuple]
        The number of phases of the ocp
    n_threads: int
        The number of thread to use if using multithreading
    original_phase_time: list[float]
        The time vector as sent by the user
    original_values: dict
        A copy of the ocp as it is after defining everything
    phase_transitions: list[PhaseTransition]
        The list of transition constraint between phases
    solver: SolverInterface
        A reference to the ocp solver
    solver_type: Solver
        The designated solver to solve the ocp
    v: OptimizationVector
        The variable optimization holder
    version: dict
        The version of all the underlying software. This is important when loading a previous ocp

    Methods
    -------
    update_objectives(self, new_objective_function: Union[Objective, ObjectiveList])
        The main user interface to add or modify objective functions in the ocp
    update_objectives_target(self, target, phase=None, list_index=None)
        Fast accessor to update the target of a specific objective function. To update target of global objective
        (usually defined by parameters), one can pass 'phase=-1
    update_constraints(self, new_constraint: Union[Constraint, ConstraintList])
        The main user interface to add or modify constraint in the ocp
    update_parameters(self, new_parameters: Union[Parameter, ParameterList])
        The main user interface to add or modify parameters in the ocp
    update_bounds(self, x_bounds: Union[Bounds, BoundsList], u_bounds: Union[Bounds, BoundsList])
        The main user interface to add bounds in the ocp
    update_initial_guess(
        self,
        x_init: Union[InitialGuess, InitialGuessList],
        u_init: Union[InitialGuess, InitialGuessList],
        param_init: Union[InitialGuess, InitialGuessList],
    )
        The main user interface to add initial guesses in the ocp
    add_plot(self, fig_name: str, update_function: Callable, phase: int = -1, **parameters: Any)
        The main user interface to add a new plot to the ocp
    prepare_plots(self, automatically_organize: bool, adapt_graph_size_to_bounds: bool,
            shooting_type: Shooting) -> PlotOCP
        Create all the plots associated with the OCP
    solve(self, solver: Solver, show_online_optim: bool, solver_options: dict) -> Solution
        Call the solver to actually solve the ocp
    save(self, sol: Solution, file_path: str, stand_alone: bool = False)
        Save the ocp and solution structure to the hard drive. It automatically create the required
        folder if it does not exists. Please note that biorbd is required to load back this structure.
    @staticmethod
    load(file_path: str) -> list
        Reload a previous optimization (*.bo) saved using save
    _define_time(self, phase_time: Union[float, tuple], objective_functions: ObjectiveList, constraints: ConstraintList)
        Declare the phase_time vector in v. If objective_functions or constraints defined a time optimization,
        a sanity check is perform and the values of initial guess and bounds for these particular phases
    __modify_penalty(self, new_penalty: Union[PenaltyOption, Parameter])
        The internal function to modify a penalty. It is also stored in the original_values, meaning that if one
        overrides an objective only the latter is preserved when saved
    """

    def __init__(
        self,
        biorbd_model: Union[str, biorbd.Model, list, tuple],
        dynamics: Union[Dynamics, DynamicsList],
        n_shooting: Union[int, list, tuple],
        phase_time: Union[int, float, list, tuple],
        x_init: Union[InitialGuess, InitialGuessList] = None,
        u_init: Union[InitialGuess, InitialGuessList] = None,
        x_bounds: Union[Bounds, BoundsList] = None,
        u_bounds: Union[Bounds, BoundsList] = None,
        objective_functions: Union[Objective, ObjectiveList] = None,
        constraints: Union[Constraint, ConstraintList] = None,
        parameters: Union[Parameter, ParameterList] = None,
        external_forces: Union[list, tuple] = None,
        ode_solver: Union[list, OdeSolverBase, OdeSolver] = None,
        control_type: Union[ControlType, list] = ControlType.CONSTANT,
        variable_mappings: BiMappingList = None,
        plot_mappings: Mapping = None,
        phase_transitions: PhaseTransitionList = None,
        n_threads: int = 1,
        use_sx: bool = False,
        skip_continuity: bool = False,
    ):
        """
        Parameters
        ----------
        biorbd_model: Union[str, biorbd.Model, list, tuple]
            The biorbd model. If biorbd_model is an str, a new model is loaded. Otherwise, the references are used
        dynamics: Union[Dynamics, DynamicsList]
            The dynamics of the phases
        n_shooting: Union[int, list[int]]
            The number of shooting point of the phases
        phase_time: Union[int, float, list, tuple]
            The phase time of the phases
        x_init: Union[InitialGuess, InitialGuessList]
            The initial guesses for the states
        u_init: Union[InitialGuess, InitialGuessList]
            The initial guesses for the controls
        x_bounds: Union[Bounds, BoundsList]
            The bounds for the states
        u_bounds: Union[Bounds, BoundsList]
            The bounds for the controls
        objective_functions: Union[Objective, ObjectiveList]
            All the objective function of the program
        constraints: Union[Constraint, ConstraintList]
            All the constraints of the program
        parameters: Union[Parameter, ParameterList]
            All the parameters to optimize of the program
        external_forces: Union[list, tuple]
            The external forces acting on the center of mass of the segments specified in the bioMod
        ode_solver: OdeSolverBase
            The solver for the ordinary differential equations
        control_type: ControlType
            The type of controls for each phase
        variable_mappings: BiMappingList
            The mapping to apply on variables
        plot_mappings: Mapping
            The mapping to apply on the plots
        phase_transitions: PhaseTransitionList
            The transition types between the phases
        n_threads: int
            The number of thread to use while solving (multi-threading if > 1)
        use_sx: bool
            The nature of the casadi variables. MX are used if False.
        skip_continuity: bool
            This is mainly for internal purposes when creating an OCP not destined to be solved
        """

        if isinstance(biorbd_model, str):
            biorbd_model = [biorbd.Model(biorbd_model)]
        elif isinstance(biorbd_model, biorbd.biorbd.Model):
            biorbd_model = [biorbd_model]
        elif isinstance(biorbd_model, (list, tuple)):
            biorbd_model = [biorbd.Model(m) if isinstance(m, str) else m for m in biorbd_model]
        else:
            raise RuntimeError("biorbd_model must either be a string or an instance of biorbd.Model()")
        self.version = {"casadi": casadi.__version__, "biorbd": biorbd.__version__, "bioptim": __version__}
        self.n_phases = len(biorbd_model)

        biorbd_model_path = [m.path().relativePath().to_string() for m in biorbd_model]

        if isinstance(dynamics, Dynamics):
            dynamics_type_tp = DynamicsList()
            dynamics_type_tp.add(dynamics)
            dynamics = dynamics_type_tp
        elif not isinstance(dynamics, DynamicsList):
            raise RuntimeError("dynamics should be a Dynamics or a DynamicsList")

        self.original_values = {
            "biorbd_model": biorbd_model_path,
            "dynamics": dynamics,
            "n_shooting": n_shooting,
            "phase_time": phase_time,
            "x_init": x_init,
            "u_init": u_init,
            "x_bounds": x_bounds,
            "u_bounds": u_bounds,
            "objective_functions": ObjectiveList(),
            "constraints": ConstraintList(),
            "parameters": ParameterList(),
            "external_forces": external_forces,
            "ode_solver": ode_solver,
            "control_type": control_type,
            "variable_mappings": variable_mappings,
            "plot_mappings": plot_mappings,
            "phase_transitions": phase_transitions,
            "n_threads": n_threads,
            "use_sx": use_sx,
        }

        # Check integrity of arguments
        if not isinstance(n_threads, int) or isinstance(n_threads, bool) or n_threads < 1:
            raise RuntimeError("n_threads should be a positive integer greater or equal than 1")

        ns = n_shooting
        if not isinstance(ns, int) or ns < 2:
            if isinstance(ns, (tuple, list)):
                if sum([True for i in ns if not isinstance(i, int) and not isinstance(i, bool)]) != 0:
                    raise RuntimeError("n_shooting should be a positive integer (or a list of) greater or equal than 2")
            else:
                raise RuntimeError("n_shooting should be a positive integer (or a list of) greater or equal than 2")

        if not isinstance(phase_time, (int, float)):
            if isinstance(phase_time, (tuple, list)):
                if sum([True for i in phase_time if not isinstance(i, (int, float))]) != 0:
                    raise RuntimeError("phase_time should be a number or a list of number")
            else:
                raise RuntimeError("phase_time should be a number or a list of number")

        if x_bounds is None:
            x_bounds = BoundsList()
        elif isinstance(x_bounds, Bounds):
            x_bounds_tp = BoundsList()
            x_bounds_tp.add(bounds=x_bounds)
            x_bounds = x_bounds_tp
        elif not isinstance(x_bounds, BoundsList):
            raise RuntimeError("x_bounds should be built from a Bounds or a BoundsList")

        if u_bounds is None:
            u_bounds = BoundsList()
        elif isinstance(u_bounds, Bounds):
            u_bounds_tp = BoundsList()
            u_bounds_tp.add(bounds=u_bounds)
            u_bounds = u_bounds_tp
        elif not isinstance(u_bounds, BoundsList):
            raise RuntimeError("u_bounds should be built from a Bounds or a BoundsList")

        if x_init is None:
            x_init = InitialGuessList()
        elif isinstance(x_init, InitialGuess):
            x_init_tp = InitialGuessList()
            x_init_tp.add(x_init)
            x_init = x_init_tp
        elif not isinstance(x_init, InitialGuessList):
            raise RuntimeError("x_init should be built from a InitialGuess or InitialGuessList")

        if u_init is None:
            u_init = InitialGuessList()
        elif isinstance(u_init, InitialGuess):
            u_init_tp = InitialGuessList()
            u_init_tp.add(u_init)
            u_init = u_init_tp
        elif not isinstance(u_init, InitialGuessList):
            raise RuntimeError("u_init should be built from a InitialGuess or InitialGuessList")

        if objective_functions is None:
            objective_functions = ObjectiveList()
        elif isinstance(objective_functions, Objective):
            objective_functions_tp = ObjectiveList()
            objective_functions_tp.add(objective_functions)
            objective_functions = objective_functions_tp
        elif not isinstance(objective_functions, ObjectiveList):
            raise RuntimeError("objective_functions should be built from an Objective or ObjectiveList")

        if constraints is None:
            constraints = ConstraintList()
        elif isinstance(constraints, Constraint):
            constraints_tp = ConstraintList()
            constraints_tp.add(constraints)
            constraints = constraints_tp
        elif not isinstance(constraints, ConstraintList):
            raise RuntimeError("constraints should be built from an Constraint or ConstraintList")

        if parameters is None:
            parameters = ParameterList()
        elif not isinstance(parameters, ParameterList):
            raise RuntimeError("parameters should be built from an ParameterList")

        if phase_transitions is None:
            phase_transitions = PhaseTransitionList()
        elif not isinstance(phase_transitions, PhaseTransitionList):
            raise RuntimeError("phase_transitions should be built from an PhaseTransitionList")

        if ode_solver is None:
            ode_solver = OdeSolver.RK4()
        elif not isinstance(ode_solver, OdeSolverBase):
            raise RuntimeError("ode_solver should be built an instance of OdeSolver")

        if not isinstance(use_sx, bool):
            raise RuntimeError("use_sx should be a bool")

        # Type of CasADi graph
        if use_sx:
            self.cx = SX
        else:
            self.cx = MX

        # Declare optimization variables
        self.J = []
        self.J_internal = []
        self.g = []
        self.g_internal = []
        self.v = OptimizationVector(self)

        # nlp is the core of a phase
        self.nlp = [NLP() for _ in range(self.n_phases)]
        NLP.add(self, "model", biorbd_model, False)
        NLP.add(self, "phase_idx", [i for i in range(self.n_phases)], False)

        # Define some aliases
        NLP.add(self, "ns", n_shooting, False)
        for nlp in self.nlp:
            if nlp.ns < 1:
                raise RuntimeError("Number of shooting points must be at least 1")

        self.n_threads = n_threads
        NLP.add(self, "n_threads", n_threads, True)
        self.solver_type = Solver.NONE
        self.solver = None

        # External forces
        if external_forces is not None:
            external_forces = BiorbdInterface.convert_array_to_external_forces(external_forces)
            NLP.add(self, "external_forces", external_forces, False)

        plot_mappings = plot_mappings if plot_mappings is not None else {}
        reshaped_plot_mappings = []
        for i in range(self.n_phases):
            reshaped_plot_mappings.append({})
            for key in plot_mappings:
                reshaped_plot_mappings[i][key] = plot_mappings[key][i]
        NLP.add(self, "plot_mapping", reshaped_plot_mappings, False, name="plot_mapping")

        # Prepare the parameters to optimize
        self.phase_transitions = []
        if len(parameters) > 0:
            self.update_parameters(parameters)

        # Declare the time to optimize
        self._define_time(phase_time, objective_functions, constraints)

        # Prepare path constraints and dynamics of the program
        NLP.add(self, "dynamics_type", dynamics, False)
        NLP.add(self, "ode_solver", ode_solver, True)
        NLP.add(self, "control_type", control_type, True)

        # Prepare the variable mappings
        if variable_mappings is None:
            variable_mappings = BiMappingList()
        NLP.add(self, "variable_mappings", variable_mappings, True)

        # Prepare the dynamics
        for i in range(self.n_phases):
            self.nlp[i].initialize(self.cx)
            ConfigureProblem.initialize(self, self.nlp[i])
            if (
                self.nlp[0].states.shape != self.nlp[i].states.shape
                or self.nlp[0].controls.shape != self.nlp[i].controls.shape
            ):
                raise RuntimeError("Dynamics with different nx or nu is not supported yet")
            self.nlp[i].ode_solver.prepare_dynamic_integrator(self, self.nlp[i])

        # Define the actual NLP problem
        self.v.define_ocp_shooting_points()

        # Define continuity constraints
        # Prepare phase transitions (Reminder, it is important that parameters are declared before,
        # otherwise they will erase the phase_transitions)
        self.phase_transitions = phase_transitions.prepare_phase_transitions(self)

        # Skipping creates a valid but unsolvable OCP class
        if not skip_continuity:
            # Inner- and inter-phase continuity
            ContinuityFunctions.continuity(self)

        self.isdef_x_init = False
        self.isdef_u_init = False
        self.isdef_x_bounds = False
        self.isdef_u_bounds = False

        self.update_bounds(x_bounds, u_bounds)
        self.update_initial_guess(x_init, u_init)

        # Prepare constraints
        self.update_constraints(constraints)

        # Prepare objectives
        self.update_objectives(objective_functions)

    def update_objectives(self, new_objective_function: Union[Objective, ObjectiveList]):
        """
        The main user interface to add or modify objective functions in the ocp

        Parameters
        ----------
        new_objective_function: Union[Objective, ObjectiveList]
            The objective to add to the ocp
        """

        if isinstance(new_objective_function, Objective):
            self.__modify_penalty(new_objective_function)

        elif isinstance(new_objective_function, ObjectiveList):
            for objective_in_phase in new_objective_function:
                for objective in objective_in_phase:
                    self.__modify_penalty(objective)

        else:
            raise RuntimeError("new_objective_function must be a Objective or an ObjectiveList")

    def update_objectives_target(self, target, phase=None, list_index=None):
        """
        Fast accessor to update the target of a specific objective function. To update target of global objective
        (usually defined by parameters), one can pass 'phase=-1'

        Parameters
        ----------
        target: np.ndarray
            The new target of the objective function. The last dimension must be the number of frames
        phase: int
            The phase the objective is in. None is interpreted as zero if the program has one phase. The value -1
            changes the values of ocp.J
        list_index: int
            The objective index
        """

        if phase is None and len(self.nlp) == 1:
            phase = 0

        if list_index is None:
            raise ValueError("'phase' must be defined")

        ObjectiveFunction.update_target(self.nlp[phase] if phase >= 0 else self, list_index, target)

    def update_constraints(self, new_constraint: Union[Constraint, ConstraintList]):
        """
        The main user interface to add or modify constraint in the ocp

        Parameters
        ----------
        new_constraint: Union[Constraint, ConstraintList]
            The constraint to add to the ocp
        """

        if isinstance(new_constraint, Constraint):
            self.__modify_penalty(new_constraint)

        elif isinstance(new_constraint, ConstraintList):
            for constraints_in_phase in new_constraint:
                for constraint in constraints_in_phase:
                    self.__modify_penalty(constraint)

        else:
            raise RuntimeError("new_constraint must be a Constraint or a ConstraintList")

    def update_parameters(self, new_parameters: Union[Parameter, ParameterList]):
        """
        The main user interface to add or modify parameters in the ocp

        Parameters
        ----------
        new_parameters: Union[Parameter, ParameterList]
            The parameters to add to the ocp
        """

        if isinstance(new_parameters, Parameter):
            self.__modify_penalty(new_parameters)

        elif isinstance(new_parameters, ParameterList):
            for parameter in new_parameters:
                self.__modify_penalty(parameter)
        else:
            raise RuntimeError("new_parameter must be a Parameter or a ParameterList")

    def update_bounds(
        self, x_bounds: Union[Bounds, BoundsList] = BoundsList(), u_bounds: Union[Bounds, BoundsList] = BoundsList()
    ):
        """
        The main user interface to add bounds in the ocp

        Parameters
        ----------
        x_bounds: Union[Bounds, BoundsList]
            The state bounds to add
        u_bounds: Union[Bounds, BoundsList]
            The control bounds to add
        """

        if x_bounds:
            NLP.add_path_condition(self, x_bounds, "x_bounds", Bounds, BoundsList)
        if u_bounds:
            NLP.add_path_condition(self, u_bounds, "u_bounds", Bounds, BoundsList)
        if self.isdef_x_bounds and self.isdef_u_bounds:
            self.v.define_ocp_bounds()

    def update_initial_guess(
        self,
        x_init: Union[InitialGuess, InitialGuessList] = InitialGuessList(),
        u_init: Union[InitialGuess, InitialGuessList] = InitialGuessList(),
        param_init: Union[InitialGuess, InitialGuessList] = InitialGuessList(),
    ):
        """
        The main user interface to add initial guesses in the ocp

        Parameters
        ----------
        x_init: Union[Bounds, BoundsList]
            The state initial guess to add
        u_init: Union[Bounds, BoundsList]
            The control initial guess to add
        param_init: Union[Bounds, BoundsList]
            The parameters initial guess to add
        """

        if x_init:
            NLP.add_path_condition(self, x_init, "x_init", InitialGuess, InitialGuessList)
        if u_init:
            NLP.add_path_condition(self, u_init, "u_init", InitialGuess, InitialGuessList)

        if isinstance(param_init, InitialGuess):
            param_init_list = InitialGuessList()
            param_init_list.add(param_init)
        else:
            param_init_list = param_init

        for param in param_init_list:
            if not param.name:
                raise ValueError("update_initial_guess must specify a name for the parameters")
            try:
                idx = self.v.parameters_in_list.index(param.name)
                self.v.parameters_in_list[idx].initial_guess.init = param.init
            except ValueError:
                raise ValueError("update_initial_guess cannot declare new parameters")

        if self.isdef_x_init and self.isdef_u_init:
            self.v.define_ocp_initial_guess()

    def add_plot(self, fig_name: str, update_function: Callable, phase: int = -1, **parameters: Any):
        """
        The main user interface to add a new plot to the ocp

        Parameters
        ----------
        fig_name: str
            The name of the figure, it the name already exists, it is merged
        update_function: Callable
            The update function callable using f(states, controls, parameters, **parameters)
        phase: int
            The phase to add the plot to. -1 is the last
        parameters: dict
            Any parameters to pass to the update_function
        """

        if "combine_to" in parameters:
            raise RuntimeError(
                "'combine_to' cannot be specified in add_plot, please use same 'fig_name' to combine plots"
            )

        # --- Solve the program --- #
        if len(self.nlp) == 1:
            phase = 0
        else:
            if phase < 0:
                raise RuntimeError("phase_idx must be specified for multiphase OCP")
        nlp = self.nlp[phase]
        custom_plot = CustomPlot(update_function, **parameters)

        plot_name = "no_name"
        if fig_name in nlp.plot:
            # Make sure we add a unique name in the dict
            custom_plot.combine_to = fig_name

            if fig_name:
                cmp = 0
                while True:
                    plot_name = f"{fig_name}_phase{phase}_{cmp}"
                    if plot_name not in nlp.plot:
                        break
                    cmp += 1
        else:
            plot_name = fig_name

        nlp.plot[plot_name] = custom_plot

    def prepare_plots(
        self,
        automatically_organize: bool = True,
        adapt_graph_size_to_bounds: bool = False,
        shooting_type: Shooting = Shooting.MULTIPLE,
    ) -> PlotOcp:
        """
        Create all the plots associated with the OCP

        Parameters
        ----------
        automatically_organize: bool
            If the graphs should be parsed on the screen
        adapt_graph_size_to_bounds: bool
            If the ylim should fit the bounds
        shooting_type: Shooting
            What type of integration

        Returns
        -------
        The PlotOcp class
        """

        return PlotOcp(
            self,
            automatically_organize=automatically_organize,
            adapt_graph_size_to_bounds=adapt_graph_size_to_bounds,
            shooting_type=shooting_type,
        )

    def solve(
        self,
        solver: Solver = Solver.IPOPT,
        show_online_optim: bool = False,
        solver_options: dict = None,
    ) -> Solution:
        """
        Call the solver to actually solve the ocp

        Parameters
        ----------
        solver: Solver
            The solver which will be used to solve the ocp
        show_online_optim: bool
            If the plot should be shown while optimizing. It will slow down the optimization a bit and is only
            available with Solver.IPOPT
        solver_options: dict
            Any options to change the behavior of the solver. To know which options are available, you can refer to the
            manual of the corresponding solver

        Returns
        -------
        The optimized solution structure
        """

        if solver == Solver.IPOPT and self.solver_type != Solver.IPOPT:
            from ..interfaces.ipopt_interface import IpoptInterface

            self.solver = IpoptInterface(self)

        elif solver == Solver.ACADOS and self.solver_type != Solver.ACADOS:
            from ..interfaces.acados_interface import AcadosInterface

            if solver_options is None:
                solver_options = {}
            self.solver = AcadosInterface(self, **solver_options)

        elif self.solver_type == Solver.NONE:
            raise RuntimeError("Solver not specified")
        self.solver_type = solver

        if show_online_optim:
            self.solver.online_optim(self)

        self.solver.configure(solver_options)
        self.solver.solve()

        return Solution(self, self.solver.get_optimized_value())

    def save(self, sol: Solution, file_path: str, stand_alone: bool = False):
        """
        Save the ocp and solution structure to the hard drive. It automatically create the required
        folder if it does not exists. Please note that biorbd is required to load back this structure.

        Parameters
        ----------
        sol: Solution
            The solution structure to save
        file_path: str
            The path to solve the structure. It creates a .bo (BiOptim file)
        stand_alone: bool
            If set to True, the variable dictionaries (states, controls and parameters) are saved instead of the full
            Solution class itself. This allows to load the saved file into a setting where bioptim is not installed
            using the pickle package, but prevents from using the class methods Solution offers after loading the file
        """

        _, ext = os.path.splitext(file_path)
        if ext == "":
            file_path = file_path + ".bo"
        elif ext != ".bo":
            raise RuntimeError(f"Incorrect extension({ext}), it should be (.bo) or (.bob) if you use save_get_data.")

        if stand_alone:
            # TODO check if this file is loaded when load is used, and raise an error
            data_to_save = sol.states, sol.controls, sol.parameters
        else:
            sol_copy = sol.copy()
            sol_copy.ocp = None  # Ocp is not pickable
            data_to_save = {"ocp_initializer": self.original_values, "sol": sol_copy, "versions": self.version}

        # Create folder if necessary
        directory, _ = os.path.split(file_path)
        if directory != "" and not os.path.isdir(directory):
            os.makedirs(directory)

        with open(file_path, "wb") as file:
            pickle.dump(data_to_save, file)

    @staticmethod
    def load(file_path: str) -> list:
        """
        Reload a previous optimization (*.bo) saved using save

        Parameters
        ----------
        file_path: str
            The path to the *.bo file

        Returns
        -------
        The ocp and sol structure. If it was saved, the iterations are also loaded
        """

        with open(file_path, "rb") as file:
            data = pickle.load(file)
            ocp = OptimalControlProgram(**data["ocp_initializer"])
            for key in data["versions"].keys():
                if data["versions"][key] != ocp.version[key]:
                    raise RuntimeError(
                        f"Version of {key} from file ({data['versions'][key]}) is not the same as the "
                        f"installed version ({ocp.version[key]})"
                    )
            sol = data["sol"]
            sol.ocp = Solution.SimplifiedOCP(ocp)
            out = [ocp, sol]
        return out

    def print(
        self,
        to_console: bool = True,
        to_graph: bool = True,
    ):

        if to_console:
            display_console = OcpToConsole(self)
            display_console.print()

        if to_graph:
            display_graph = OcpToGraph(self)
            display_graph.print()

    def _define_time(
        self,
        phase_time: Union[int, float, list, tuple],
        objective_functions: ObjectiveList,
        constraints: ConstraintList,
    ):
        """
        Declare the phase_time vector in v. If objective_functions or constraints defined a time optimization,
        a sanity check is perform and the values of initial guess and bounds for these particular phases

        Parameters
        ----------
        phase_time: Union[int, float, list, tuple]
            The time of all the phases
        objective_functions: ObjectiveList
            All the objective functions. It is used to scan if any time optimization was defined
        constraints: ConstraintList
            All the constraint functions. It is used to scan if any free time was defined
        """

        def define_parameters_phase_time(
            ocp: OptimalControlProgram,
            penalty_functions: Union[ObjectiveList, ConstraintList],
            _initial_time_guess: list,
            _phase_time: list,
            _time_min: list,
            _time_max: list,
            _has_penalty: list = None,
        ) -> list:
            """
            Sanity check to ensure that only one time optimization is defined per phase. It also creates the time vector
            for initial guesses and bounds

            Parameters
            ----------
            ocp: OptimalControlProgram
                A reference to the ocp
            penalty_functions: Union[ObjectiveList, ConstraintList]
                The list to parse to ensure no double free times are declared
            _initial_time_guess: list
                The list of all initial guesses for the free time optimization
            _phase_time: list
                Replaces the values where free time is found for MX or SX
            _time_min: list
                Minimal bounds for the time parameter
            _time_max: list
                Maximal bounds for the time parameter
            _has_penalty: list[bool]
                If a penalty was previously found. This should be None on the first call to ensure proper initialization

            Returns
            -------
            The state of has_penalty
            """

            if _has_penalty is None:
                _has_penalty = [False] * ocp.n_phases

            for i, penalty_functions_phase in enumerate(penalty_functions):
                for pen_fun in penalty_functions_phase:
                    if not pen_fun:
                        continue
                    if (
                        pen_fun.type == ObjectiveFcn.Mayer.MINIMIZE_TIME
                        or pen_fun.type == ObjectiveFcn.Lagrange.MINIMIZE_TIME
                        or pen_fun.type == ConstraintFcn.TIME_CONSTRAINT
                    ):
                        if _has_penalty[i]:
                            raise RuntimeError("Time constraint/objective cannot declare more than once")
                        _has_penalty[i] = True

                        _initial_time_guess.append(_phase_time[i])
                        _phase_time[i] = ocp.cx.sym(f"time_phase_{i}", 1, 1)
                        if pen_fun.type.get_type() == ConstraintFunction:
                            _time_min.append(pen_fun.min_bound if pen_fun.min_bound else 0)
                            _time_max.append(pen_fun.max_bound if pen_fun.max_bound else inf)
                        else:
                            _time_min.append(pen_fun.params["min_bound"] if "min_bound" in pen_fun.params else 0)
                            _time_max.append(pen_fun.params["max_bound"] if "max_bound" in pen_fun.params else inf)
            return _has_penalty

        NLP.add(self, "t_initial_guess", phase_time, False)
        self.original_phase_time = phase_time
        if isinstance(phase_time, (int, float)):
            phase_time = [phase_time]
        phase_time = list(phase_time)
        initial_time_guess, time_min, time_max = [], [], []
        has_penalty = define_parameters_phase_time(
            self, objective_functions, initial_time_guess, phase_time, time_min, time_max
        )
        define_parameters_phase_time(
            self, constraints, initial_time_guess, phase_time, time_min, time_max, _has_penalty=has_penalty
        )

        # Add to the nlp
        NLP.add(self, "tf", phase_time, False)
        NLP.add(self, "t0", [0] + [nlp.tf for i, nlp in enumerate(self.nlp) if i != len(self.nlp) - 1], False)
        NLP.add(self, "dt", [self.nlp[i].tf / max(self.nlp[i].ns, 1) for i in range(self.n_phases)], False)

        # Add to the v vector
        i = 0
        for nlp in self.nlp:
            if isinstance(nlp.tf, self.cx):
                time_bounds = Bounds(time_min[i], time_max[i], interpolation=InterpolationType.CONSTANT)
                time_init = InitialGuess(initial_time_guess[i])
                time_param = Parameter(
                    cx=nlp.tf, function=None, size=1, bounds=time_bounds, initial_guess=time_init, name="time"
                )
                self.v.add_parameter(time_param)
                i += 1

    def __modify_penalty(self, new_penalty: Union[PenaltyOption, Parameter]):
        """
        The internal function to modify a penalty. It is also stored in the original_values, meaning that if one
        overrides an objective only the latter is preserved when saved

        Parameters
        ----------
        new_penalty: PenaltyOption
            Any valid option to add to the program
        """

        if not new_penalty:
            return
        phase_idx = new_penalty.phase

        # Copy to self.original_values so it can be save/load
        pen = new_penalty.type.get_type()
        self.original_values[pen.penalty_nature()].add(deepcopy(new_penalty))
        new_penalty.add_or_replace_to_penalty_pool(self, self.nlp[phase_idx])
