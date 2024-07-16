# Copyright 2021 The Cirq Developers
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tooling for interleaved randomised benchmarking
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from typing import NamedTuple, cast

import cirq
import numpy as np
import pandas as pd
import seaborn as sns
from scipy.stats import linregress
from tqdm.contrib.itertools import product

from cirq_superstaq.qcvv.base_experiment import BenchmarkingExperiment, Sample


class IRBResults(NamedTuple):
    """Data structure for the IRB experiment results."""

    rb_layer_fidelity: float
    """Layer fidelity estimate without the interleaving gate."""
    rb_layer_fidelity_std: float
    """Standard deviation of the layer fidelity estimate without the interleaving gate."""
    irb_layer_fidelity: float
    """Layer fidelity estimate with the interleaving gate."""
    irb_layer_fidelity_std: float
    """Standard deviation of the layer fidelity estimate with the interleaving gate."""
    average_interleaved_gate_error: float
    """Estimate of the interleaving gate error."""
    average_interleaved_gate_error_std: float
    """Standard deviation of the estimate for the interleaving gate error."""


class IRB(BenchmarkingExperiment):
    r"""Interleaved random benchmarking (IRB) experiment.

    IRB estimates the gate error of specified Clifford gate, :math:`\mathcal{C}^*`.
    This is achieved by first choosing a random sequence, :math:`\{\mathcal{C_i}\}_m`
    of :math:`m` Clifford gates and then using this to generate two circuits. The first
    is generated by appending to this sequence the single gate that corresponds to the
    inverse of the original sequence. The second circuit it obtained by inserting the
    interleaving gate, :math:`\mathcal{C}^*` after each gate in the sequence and then
    again appending the corresponding inverse element of the new circuit. Thus both
    circuits correspond to the identity operation.

    We run both circuits on the specified target and calculate the probability of measuring
    the resulting state in the ground state, :math:`p(0...0)`. This gives the circuit fidelity

    .. math::

        f(m) = 2p(0...0) - 1

    We can then fit and exponential decay :math:`\log(f) \sim m` to this circuit fidelity
    for each circuit, with decay rates :math:`\alpha` and :math:`\tilde{\alpha}` for the circuit
    without and with interleaving respectively. Finally the gate error of the
    specified gate, :math:`\mathcal{C}^*` is estimated as

    .. math::

        e_{\mathcal{C}^*} = 1 - \frac{\tilde{\alpha}}{\alpha}

    """

    def __init__(
        self,
        interleaved_gate: cirq.ops.SingleQubitCliffordGate = cirq.ops.SingleQubitCliffordGate.Z,
        num_qubits: int = 1,
    ) -> None:
        """Args:
        interleaved_gate: The Clifford gate to measure the gate error of.
        num_qubits: The number of qubits to experiment on
        """
        if num_qubits != 1:
            raise NotImplementedError(
                "IRB experiment is currently only implemented for single qubit use"
            )
        super().__init__(num_qubits=1)

        self.interleaved_gate = interleaved_gate
        """The gate being interleaved"""

    @property
    def results(self) -> IRBResults:
        """The results from the most recently run experiment"""
        return cast("IRBResults", super().results)

    @staticmethod
    def _reduce_clifford_seq(
        gate_seq: list[cirq.ops.SingleQubitCliffordGate],
    ) -> cirq.ops.SingleQubitCliffordGate:
        """Reduces a list of single qubit clifford gates to a single gate.

        Args:
            gate_seq: The list of gates.

        Returns:
            The single reduced gate
        """
        cur = gate_seq[0]
        for gate in gate_seq[1:]:
            cur = cur.merged_with(gate)
        return cur

    @classmethod
    def _random_single_qubit_clifford(cls) -> cirq.ops.SingleQubitCliffordGate:
        """Choose a random singe qubit clifford gate.

        Returns:
            The random clifford gate.
        """
        Id = cirq.ops.SingleQubitCliffordGate.I
        H = cirq.ops.SingleQubitCliffordGate.H
        S = cirq.ops.SingleQubitCliffordGate.Z_sqrt
        X = cirq.ops.SingleQubitCliffordGate.X
        Y = cirq.ops.SingleQubitCliffordGate.Y
        Z = cirq.ops.SingleQubitCliffordGate.Z

        set_A = [
            Id,
            S,
            H,
            cls._reduce_clifford_seq([H, S]),
            cls._reduce_clifford_seq([S, H]),
            cls._reduce_clifford_seq([H, S, H]),
        ]

        set_B = [Id, X, Y, Z]

        return cls._reduce_clifford_seq([random.choice(set_A), random.choice(set_B)])

    def _invert_clifford_circuit(self, circuit: cirq.Circuit) -> cirq.Circuit:
        """Given a Clifford circuit find and append the corresponding inverse Clifford gate

        Args:
            circuit: The Clifford circuit to invert.

        Returns:
            A copy of the original Clifford circuit with the inverse element appended.
        """
        clifford_gates = [op.gate for op in circuit.all_operations()]
        inv_element = self._reduce_clifford_seq(
            cirq.inverse(clifford_gates)  # type: ignore[arg-type]
        )
        clifford_gates.append(inv_element)
        return cirq.Circuit(*[gate(*self.qubits) for gate in clifford_gates])  # type: ignore[misc]

    def build_circuits(self, num_circuits: int, layers: Iterable[int]) -> Sequence[Sample]:
        """Build a list of randomised circuits required for the IRB experiment.
        These circuits do not include the interleaving gate or the final inverse
        gate, instead these are added in the :meth:`sample_circuit` function.

        Args:
            num_circuits: Number of circuits to generate.
            layers: TODO

        Returns:
            TODO
        """
        samples = []
        for _, depth in product(range(num_circuits), layers, desc="Building circuits"):
            base_circuit = cirq.Circuit(
                *[self._random_single_qubit_clifford()(*self.qubits) for _ in range(depth)]
            )
            rb_circuit = self._invert_clifford_circuit(base_circuit)
            irb_circuit = self._invert_clifford_circuit(
                self._interleave_gate(base_circuit, self.interleaved_gate, include_final=True)
            )
            samples += [
                Sample(
                    circuit=rb_circuit,
                    data={
                        "num_cycles": depth,
                        "circuit_depth": len(rb_circuit),
                        "experiment": "RB",
                    },
                ),
                Sample(
                    circuit=irb_circuit,
                    data={
                        "num_cycles": depth,
                        "circuit_depth": len(irb_circuit),
                        "experiment": "IRB",
                    },
                ),
            ]

        return samples

    def process_probabilities(self) -> None:
        """Processes the probabilities generated by sampling the circuits into the data structures
        needed for analyzing the results.
        """
        super().process_probabilities()

        records = []
        for sample in self.samples:
            records.append(
                {
                    "clifford_depth": sample.data["num_cycles"],
                    "circuit_depth": sample.data["circuit_depth"],
                    "experiment": sample.data["experiment"],
                    **sample.probabilities,
                }
            )

        self._raw_data = pd.DataFrame(records)

    def plot_results(self) -> None:
        """Plot the exponential decay of the circuit fidelity with
        cycle depth.
        """
        plot = sns.lmplot(
            data=self.raw_data,
            x="clifford_depth",
            y="log_fidelity",
            hue="experiment",
        )
        ax = plot.axes.item()
        plot.tight_layout()
        ax.set_xlabel(r"Cycle depth", fontsize=15)
        ax.set_ylabel(r"Log Circuit fidelity", fontsize=15)
        ax.set_title(r"Exponential decay of circuit fidelity", fontsize=15)

    def analyse_results(self, plot_results: bool = True) -> IRBResults:
        """Analyse the experiment results and estimate the interleaved gate error."""

        self.raw_data["fidelity"] = 2 * self.raw_data["0"] - 1
        self.raw_data["log_fidelity"] = np.log(self.raw_data["fidelity"])

        rb_model = linregress(
            self.raw_data.query("experiment == 'RB'")["clifford_depth"],
            np.log(self.raw_data.query("experiment == 'RB'")["fidelity"]),
        )
        irb_model = linregress(
            self.raw_data.query("experiment == 'IRB'")["clifford_depth"],
            np.log(self.raw_data.query("experiment == 'IRB'")["fidelity"]),
        )

        # Extract fit values.
        rb_layer_fidelity = np.exp(rb_model.slope)
        rb_layer_fidelity_std = rb_model.stderr * rb_layer_fidelity
        irb_layer_fidelity = np.exp(irb_model.slope)
        irb_layer_fidelity_std = irb_model.stderr * irb_layer_fidelity

        interleaved_gate_error = (1 - irb_layer_fidelity / rb_layer_fidelity) / 2
        interleaved_gate_error_std = interleaved_gate_error * np.sqrt(
            (rb_layer_fidelity_std / rb_layer_fidelity) ** 2
            + (irb_layer_fidelity_std / irb_layer_fidelity) ** 2
        )

        self._results = IRBResults(
            rb_layer_fidelity=rb_layer_fidelity,
            rb_layer_fidelity_std=rb_layer_fidelity_std,
            irb_layer_fidelity=irb_layer_fidelity,
            irb_layer_fidelity_std=irb_layer_fidelity_std,
            average_interleaved_gate_error=interleaved_gate_error,
            average_interleaved_gate_error_std=interleaved_gate_error_std,
        )

        if plot_results:
            self.plot_results()

        return self.results
