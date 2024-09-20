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
from dataclasses import dataclass
from typing import Union  # noqa: MDA400

import cirq
import numpy as np
import pandas as pd
import seaborn as sns
from scipy.stats import linregress
from tqdm.contrib.itertools import product

from supermarq.qcvv.base_experiment import BenchmarkingExperiment, BenchmarkingResults, Sample


@dataclass(frozen=True)
class IRBResults(BenchmarkingResults):
    """Data structure for the IRB experiment results."""

    rb_decay_coefficient: float
    """Decay coefficient estimate without the interleaving gate."""
    rb_decay_coefficient_std: float
    """Standard deviation of the decay coefficient estimate without the interleaving gate."""
    irb_decay_coefficient: float | None
    """Decay coefficient estimate with the interleaving gate."""
    irb_decay_coefficient_std: float | None
    """Standard deviation of the decay coefficient estimate with the interleaving gate."""
    average_interleaved_gate_error: float | None
    """Estimate of the interleaving gate error."""
    average_interleaved_gate_error_std: float | None
    """Standard deviation of the estimate for the interleaving gate error."""

    experiment_name = "IRB"


@dataclass(frozen=True)
class RBResults(BenchmarkingResults):
    """Data structure for the RB experiment results."""

    rb_decay_coefficient: float
    """Decay coefficient estimate without the interleaving gate."""
    rb_decay_coefficient_std: float
    """Standard deviation of the decay coefficient estimate without the interleaving gate."""
    average_gate_error: float | None
    """Estimate of the average gate error."""
    average_gate_error_std: float | None
    """Standard deviation of the estimate for the average gate error."""

    experiment_name = "RB"


class IRB(BenchmarkingExperiment[Union[IRBResults, RBResults]]):
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

    We can then fit an exponential decay :math:`\log(f) \sim m` to this circuit fidelity
    for each circuit, with decay rates :math:`\alpha` and :math:`\tilde{\alpha}` for the circuit
    without and with interleaving respectively. Finally the gate error of the
    specified gate, :math:`\mathcal{C}^*` is estimated as

    .. math::

        e_{\mathcal{C}^*} = \frac{1}{2} \left(1 - \frac{\tilde{\alpha}}{\alpha}\right)

    For more details see: https://arxiv.org/abs/1203.4550
    """

    def __init__(
        self,
        interleaved_gate: (
            cirq.ops.SingleQubitCliffordGate | None
        ) = cirq.ops.SingleQubitCliffordGate.Z,
        num_qubits: int = 1,
    ) -> None:
        """Constructs an IRB experiment.

        Args:
            interleaved_gate: The single qubit Clifford gate to measure the gate error of. If None
                then no interleaving is performed and instead vanilla Randomize benchmarking is
                performed.
            num_qubits: The number of qubits to experiment on
        """
        if num_qubits != 1:
            raise NotImplementedError(
                "IRB experiment is currently only implemented for single qubit use"
            )
        super().__init__(num_qubits=1)

        self.interleaved_gate = interleaved_gate
        """The gate being interleaved"""

    @staticmethod
    def _reduce_clifford_seq(
        gate_seq: list[cirq.ops.SingleQubitCliffordGate],
    ) -> cirq.ops.SingleQubitCliffordGate:
        """Reduces a list of single qubit clifford gates to a single gate.

        Args:
            gate_seq: The list of gates.
            The single reduced gate.
        Returns:
            The single reduced gate
        """
        cur = gate_seq[0]
        for gate in gate_seq[1:]:
            cur = cur.merged_with(gate)
        return cur

    @classmethod
    def _random_single_qubit_clifford(cls) -> cirq.ops.SingleQubitCliffordGate:
        """Choose a random single qubit clifford gate.

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
        """Given a Clifford circuit find and append the corresponding inverse Clifford gate.

        Args:
            circuit: The Clifford circuit to invert.

        Returns:
            A copy of the original Clifford circuit with the inverse element appended.
        """
        clifford_gates = [op.gate for op in circuit.all_operations()]
        inv_element = self._reduce_clifford_seq(
            cirq.inverse(clifford_gates)  # type: ignore[arg-type]
        )
        return circuit + inv_element(*self.qubits)

    def _build_circuits(self, num_circuits: int, cycle_depths: Iterable[int]) -> Sequence[Sample]:
        """Build a list of randomised circuits required for the IRB experiment.

        Args:
            num_circuits: Number of circuits to generate.
            cycle_depths: An iterable of the different cycle depths to use during the experiment.

        Returns:
            The list of experiment samples.
        """
        samples = []
        for _, depth in product(range(num_circuits), cycle_depths, desc="Building circuits"):
            base_circuit = cirq.Circuit(
                *[self._random_single_qubit_clifford()(*self.qubits) for _ in range(depth)]
            )
            rb_circuit = self._invert_clifford_circuit(base_circuit)
            samples.append(
                Sample(
                    raw_circuit=rb_circuit + cirq.measure(sorted(rb_circuit.all_qubits())),
                    data={
                        "num_cycles": depth,
                        "circuit_depth": len(rb_circuit),
                        "experiment": "RB",
                    },
                ),
            )
            if self.interleaved_gate is not None:
                irb_circuit = self._invert_clifford_circuit(
                    self._interleave_op(
                        base_circuit, self.interleaved_gate(*self.qubits), include_final=True
                    )
                )
                samples.append(
                    Sample(
                        raw_circuit=irb_circuit + cirq.measure(sorted(irb_circuit.all_qubits())),
                        data={
                            "num_cycles": depth,
                            "circuit_depth": len(irb_circuit),
                            "experiment": "IRB",
                        },
                    ),
                )
        return samples

    def _process_probabilities(self, samples: Sequence[Sample]) -> pd.DataFrame:
        """Processes the probabilities generated by sampling the circuits into the data structures
        needed for analyzing the results.

        Args:
            samples: The list of samples to process the results from.

        Returns:
            A data frame of the full results needed to analyse the experiment.
        """

        records = []
        for sample in samples:
            records.append(
                {
                    "clifford_depth": sample.data["num_cycles"],
                    "circuit_depth": sample.data["circuit_depth"],
                    "experiment": sample.data["experiment"],
                    **sample.probabilities,
                }
            )

        return pd.DataFrame(records)

    def plot_results(self) -> None:
        """Plot the exponential decay of the circuit fidelity with cycle depth."""
        plot = sns.lmplot(
            data=self.raw_data,
            x="clifford_depth",
            y="log_survival_prob",
            hue="experiment",
        )
        ax = plot.axes.item()
        plot.tight_layout()
        ax.set_xlabel(r"Cycle depth", fontsize=15)
        ax.set_ylabel(r"Log survival probability", fontsize=15)
        ax.set_title(r"Exponential decay of survival probability", fontsize=15)

    def analyze_results(self, plot_results: bool = True) -> IRBResults | RBResults:
        """Analyse the experiment results and estimate the interleaved gate error.

        Args:
            plot_results: Whether to generate plots of the results. Defaults to False.

        Returns:
            A named tuple of the final results from the experiment.
        """

        self.raw_data["survival_prob"] = 2 * self.raw_data["0"] - 1
        self.raw_data["log_survival_prob"] = np.log(self.raw_data["survival_prob"])
        self.raw_data.dropna(axis=0, inplace=True)  # Remove any NaNs coming from the P(0) < 0.5

        rb_model = linregress(
            self.raw_data.query("experiment == 'RB'")["clifford_depth"],
            np.log(self.raw_data.query("experiment == 'RB'")["survival_prob"]),
        )
        rb_decay_coefficient = np.exp(rb_model.slope)
        rb_decay_coefficient_std = rb_model.stderr * rb_decay_coefficient

        if self.interleaved_gate is None:
            self._results = RBResults(
                target="& ".join(self.targets),
                total_circuits=len(self.samples),
                rb_decay_coefficient=rb_decay_coefficient,
                rb_decay_coefficient_std=rb_decay_coefficient_std,
                average_gate_error=(1 - 2**-self.num_qubits) * (1 - rb_decay_coefficient),
                average_gate_error_std=(1 - 2**-self.num_qubits) * rb_decay_coefficient_std,
            )

            if plot_results:
                self.plot_results()

            return self.results

        else:
            irb_model = linregress(
                self.raw_data.query("experiment == 'IRB'")["clifford_depth"],
                np.log(self.raw_data.query("experiment == 'IRB'")["survival_prob"]),
            )

            # Extract fit values.
            irb_decay_coefficient = np.exp(irb_model.slope)
            irb_decay_coefficient_std = irb_model.stderr * irb_decay_coefficient

            interleaved_gate_error = (1 - irb_decay_coefficient / rb_decay_coefficient) * (
                1 - 2**-self.num_qubits
            )

            interleaved_gate_error_std = np.sqrt(
                (irb_decay_coefficient_std / (2 * rb_decay_coefficient)) ** 2
                + (
                    (irb_decay_coefficient * rb_decay_coefficient_std)
                    / (2 * rb_decay_coefficient**2)
                )
                ** 2
            )

            self._results = IRBResults(
                target="& ".join(self.targets),
                total_circuits=len(self.samples),
                rb_decay_coefficient=rb_decay_coefficient,
                rb_decay_coefficient_std=rb_decay_coefficient_std,
                irb_decay_coefficient=irb_decay_coefficient,
                irb_decay_coefficient_std=irb_decay_coefficient_std,
                average_interleaved_gate_error=interleaved_gate_error,
                average_interleaved_gate_error_std=interleaved_gate_error_std,
            )

            if plot_results:
                self.plot_results()

            return self.results
