import copy
from typing import Dict, List, Tuple, Union

import cirq
from geneticalgorithm import geneticalgorithm as ga
import numpy as np
import numpy.typing as npt
import scipy.optimize as opt

import supermarq


class VQE_HWEA_Clifford(supermarq.benchmark.Benchmark):
    """Proxy benchmark of a clifford-only VQE application that targets a single iteration
    of the whole variational optimization. The goals of this benchmark: 
    
    1) Provide a means for VQA optimization via efficient Clifford simulation 
    (i.e. it can  be used to warm start VQE).

    2) Outcomes on a quantum machine can be easily verified since resulting circuits are 
    easily simulatable.

    The benchmark is parameterized by the number of qubits, n. For each value of
    n, we classically optimize the Clifford-only ansatz, sample 3 iterations near convergence,
    and use the sampled parameters to execute the corresponding circuits on the
    QPU. We take the measured energies from these experiments and average their
    values and compute a score based on how closely the experimental results are
    to the noiseless values.
    """

    def __init__(self, num_qubits: int, num_layers: int = 1) -> None:
        self.num_qubits = num_qubits
        self.num_layers = num_layers
        self.hamiltonian = self._gen_tfim_hamiltonian()
        self._params = self._gen_angles()

    def _gen_tfim_hamiltonian(self) -> List[Tuple[str, Union[int, Tuple[int, int]], int]]:
        r"""Generate an n-qubit Hamiltonian for a transverse-field Ising model (TFIM).

            $H = \sum_i^n(X_i) + \sum_i^n(Z_i Z_{i+1})$

        Example of a 6-qubit TFIM Hamiltonian:

            $H_6 = XIIIII + IXIIII + IIXIII + IIIXII + IIIIXI + IIIIIX + ZZIIII
                  + IZZIII + IIZZII + IIIZZI + IIIIZZ + ZIIIIZ$
        """
        hamiltonian: List[Tuple[str, Union[int, Tuple[int, int]], int]] = []
        for i in range(self.num_qubits):
            hamiltonian.append(("X", i, 1))  # [Pauli type, qubit idx, weight]
        for i in range(self.num_qubits - 1):
            hamiltonian.append(("ZZ", (i, i + 1), 1))
        hamiltonian.append(("ZZ", (self.num_qubits - 1, 0), 1))
        return hamiltonian

    def _gen_ansatz(self, params: npt.NDArray[np.float_]) -> List[cirq.Circuit]:
        qubits = cirq.LineQubit.range(self.num_qubits)
        z_circuit = cirq.Circuit()

        param_counter = 0
        for _ in range(self.num_layers):
            # Ry rotation block
            for i in range(self.num_qubits):
                z_circuit.append((cirq.Y**params[param_counter])(qubits[i]))
                param_counter += 1
            # Rz rotation block
            for i in range(self.num_qubits):
                z_circuit.append((cirq.Z**params[param_counter])(qubits[i]))
                param_counter += 1
            # Entanglement block
            for i in range(self.num_qubits - 1):
                z_circuit.append(cirq.CX(qubits[i], qubits[i + 1]))
            # Ry rotation block
            for i in range(self.num_qubits):
                z_circuit.append((cirq.Y**params[param_counter])(qubits[i]))
                param_counter += 1
            # Rz rotation block
            for i in range(self.num_qubits):
                z_circuit.append((cirq.Z**params[param_counter])(qubits[i]))
                param_counter += 1

        x_circuit = copy.deepcopy(z_circuit)
        x_circuit.append(cirq.H(q) for q in qubits)

        # Measure all qubits
        z_circuit.append(cirq.measure(*qubits))
        x_circuit.append(cirq.measure(*qubits))

        return [z_circuit, x_circuit]

    def _parity_ones(self, bitstr: str) -> int:
        one_count = 0
        for i in bitstr:
            if i == "1":
                one_count += 1
        return one_count % 2

    def _calc(self, bit_list: List[str], bitstr: str, probs: Dict[str, float]) -> float:
        energy = 0.0
        for item in bit_list:
            if self._parity_ones(item) == 0:
                energy += probs.get(bitstr, 0)
            else:
                energy -= probs.get(bitstr, 0)
        return energy

    def _get_expectation_value_from_probs(
        self, probs_z: Dict[str, float], probs_x: Dict[str, float]
    ) -> float:
        avg_energy = 0.0

        # Find the contribution to the energy from the X-terms: \sum_i{X_i}
        for bitstr in probs_x.keys():
            bit_list_x = [bitstr[i] for i in range(len(bitstr))]
            avg_energy += self._calc(bit_list_x, bitstr, probs_x)

        # Find the contribution to the energy from the Z-terms: \sum_i{Z_i Z_{i+1}}
        for bitstr in probs_z.keys():
            # fmt: off
            bit_list_z = [bitstr[(i - 1): (i + 1)] for i in range(1, len(bitstr))]
            # fmt: on
            bit_list_z.append(bitstr[0] + bitstr[-1])  # Add the wrap-around term manually
            avg_energy += self._calc(bit_list_z, bitstr, probs_z)

        return avg_energy

    def _get_opt_angles(self) -> Tuple[npt.NDArray[np.float_], float]:
        def f(params: npt.NDArray[np.float_]) -> float:
            print(params)
            params = params * 1/2
            #clifford_angles = np.array([0, 0.5, 1, 1.5, 2])*np.pi
            new_params = np.array([find_closest(clifford_angles, i) for i in params])
            print(new_params)
            z_circuit, x_circuit = self._gen_ansatz(new_params)
            z_probs = supermarq.simulation.get_ideal_counts_clifford(z_circuit)
            x_probs = supermarq.simulation.get_ideal_counts_clifford(x_circuit)
            energy = self._get_expectation_value_from_probs(z_probs, x_probs)
            print(-energy)
            return -energy  # because we are minimizing instead of maximizing

        def find_closest(arr, val):
            index = np.abs(arr - val).argmin()
            return arr[index]
        
        
        def f_clifford_constraint(params: npt.NDArray[np.float_],
                          clifford_angles: npt.NDArray[np.float_]) -> bool:
                return np.all(np.isin(params,clifford_angles),where=True)

        clifford_angles = np.array([0, 1, 2, 3, 4])

        init_params = list(np.random.choice(clifford_angles, 4 * self.num_qubits * self.num_layers))
        opt_bounds = np.array([(0,4)]*4*self.num_qubits*self.num_layers)#opt.Bounds(lb=0,ub=np.pi*2)
        ga_defaults = {'max_num_iteration': 30, 'population_size': 100, 'mutation_probability': 0.1, 'elit_ratio': 0.01, 'crossover_probability': 0.5, 'parents_portion': 0.3, 'crossover_type': 'uniform', 'max_iteration_without_improv': None}        
        #out = opt.minimize(f, init_params,bounds=opt_bounds, method="L-BFGS-B")
        #out = opt.shgo(f,bounds=opt_bounds)
        model = ga(function=f,dimension=len(init_params),algorithm_parameters= ga_defaults,variable_type='int',variable_boundaries=opt_bounds,convergence_curve=False,progress_bar=False)
        model.run()
        best_params = model.best_variable
        print(best_params)
        
        best_params = best_params * 1/2

        return best_params, model.best_function
    def _gen_angles(self) -> npt.NDArray[np.float_]:
        """Classically simulate the variational optimization and return
        the final parameters.
        """
        params, _ = self._get_opt_angles()
        return params

    def circuit(self) -> List[cirq.Circuit]:
        """Construct a parameterized ansatz.

        Returns a list of circuits: the ansatz measured in the Z basis, and the
        ansatz measured in the X basis. The counts obtained from evaluated these
        two circuits should be passed to `score` in the same order they are
        returned here.
        """
        return self._gen_ansatz(self._params)

    def score(self, counts: List[Dict[str, float]]) -> float:
        """Compare the average energy measured by the experiments to the ideal
        value obtained via noiseless simulation. In principle the ideal value
        can be obtained through efficient classical means since the 1D TFIM
        is analytically solvable.
        """
        counts_z, counts_x = counts
        shots_z = sum(counts_z.values())
        probs_z = {bitstr: count / shots_z for bitstr, count in counts_z.items()}
        shots_x = sum(counts_x.values())
        probs_x = {bitstr: count / shots_x for bitstr, count in counts_x.items()}
        experimental_expectation = self._get_expectation_value_from_probs(probs_z, probs_x)

        circuit_z, circuit_x = self.circuit()
        ideal_expectation = self._get_expectation_value_from_probs(
            supermarq.simulation.get_ideal_counts(circuit_z),
            supermarq.simulation.get_ideal_counts(circuit_x),
        )

        return float(
            1.0 - abs(ideal_expectation - experimental_expectation) / abs(2 * ideal_expectation)
        )
