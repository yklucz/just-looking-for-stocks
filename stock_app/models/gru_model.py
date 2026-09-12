"""Independent GRU binary challenger with training-only unique-row standardization."""

from dataclasses import asdict
import json
import logging
from pathlib import Path
from time import perf_counter
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
import torch
from torch import nn
from torch.utils.data import DataLoader

from ..config import GRUConfig
from ..training.sequence_dataset import SequenceDataset, SequenceInput
from .base import BinaryClassifier, class_balance
from .torch_utils import (
    configure_cpu_threads,
    get_torch_device,
    set_random_seeds,
    synchronize,
)


logger = logging.getLogger(__name__)


class GRUNetwork(nn.Module):
    def __init__(
        self,
        feature_count: int,
        config: GRUConfig,
    ):
        super().__init__()

        configure_cpu_threads(
            config.cpu_threads
        )

        self.projection = nn.Sequential(
            nn.Linear(
                feature_count,
                config.projection_size,
            ),
            nn.LayerNorm(
                config.projection_size
            ),
        )

        self.gru = nn.GRU(
            config.projection_size,
            config.hidden_size,
            num_layers=config.num_layers,
            dropout=(
                config.dropout
                if config.num_layers > 1
                else 0.0
            ),
            batch_first=True,
        )

        self.head = nn.Sequential(
            nn.Linear(
                config.hidden_size,
                config.head_size,
            ),
            nn.GELU(),
            nn.Dropout(
                config.dropout
            ),
            nn.Linear(
                config.head_size,
                1,
            ),
        )

    def forward(
        self,
        x: torch.Tensor,
    ) -> torch.Tensor:
        _, hidden = self.gru(
            self.projection(x)
        )

        return self.head(
            hidden[-1]
        ).squeeze(-1)


class GRUClassifier(BinaryClassifier):
    model_type = "gru"
    uses_validation = True
    sequential = True

    def __init__(
        self,
        config: GRUConfig = GRUConfig(),
    ):
        self.config = config
        self.sequence_length = (
            config.sequence_length
        )
        self.feature_names: tuple[str, ...] = ()

    def _require_sequence_input(
        self,
        value: Any,
    ) -> SequenceInput:
        if not isinstance(
            value,
            SequenceInput,
        ):
            raise ValueError(
                "GRU requires SequenceInput"
            )

        return value

    def _check_sequence(
        self,
        value: Any,
    ) -> SequenceInput:
        inputs = self._require_sequence_input(
            value
        )

        if (
            tuple(inputs.columns)
            != self.feature_names
            or inputs.sequence_length
            != self.sequence_length
        ):
            raise ValueError(
                "GRU feature order or sequence length mismatch"
            )

        return inputs

    @staticmethod
    def _validate_labels(
        inputs: SequenceInput,
        labels: pd.Series,
        *,
        both_classes: bool = False,
    ) -> None:
        if len(inputs.index) != len(labels):
            raise ValueError(
                "Feature and target lengths differ"
            )

        if not labels.index.equals(
            inputs.index
        ):
            raise ValueError(
                "Feature and target indices differ"
            )

        if labels.isna().any():
            raise ValueError(
                "Target contains missing values"
            )

        unique = set(
            labels.astype(int).unique()
        )

        if not unique.issubset(
            {0, 1}
        ):
            raise ValueError(
                "Binary target must contain only 0 and 1"
            )

        if (
            both_classes
            and unique != {0, 1}
        ):
            raise ValueError(
                "Training target must contain both classes"
            )

    @property
    def parameter_count(self) -> int:
        return sum(
            parameter.numel()
            for parameter
            in self.network.parameters()
            if parameter.requires_grad
        )

    def _prepare_training_inputs(
        self,
        inputs: Any,
        labels: pd.Series,
        validation: Any,
    ) -> tuple[
        SequenceInput,
        pd.Series,
        SequenceInput,
        pd.Series,
    ]:
        train_inputs = (
            self._require_sequence_input(
                inputs
            )
        )

        if train_inputs.scaler_rows is None:
            raise ValueError(
                "GRU training requires unique training feature rows"
            )

        self.feature_names = tuple(
            train_inputs.columns
        )

        train_inputs = self._check_sequence(
            train_inputs
        )

        self._validate_labels(
            train_inputs,
            labels,
            both_classes=True,
        )

        if validation is None:
            raise ValueError(
                "Chronological validation required"
            )

        (
            validation_inputs_raw,
            validation_labels,
        ) = validation

        validation_inputs = (
            self._check_sequence(
                validation_inputs_raw
            )
        )

        self._validate_labels(
            validation_inputs,
            validation_labels,
        )

        scaler_rows = (
            train_inputs.scaler_rows
        )

        if scaler_rows is None:
            raise ValueError(
                "Missing training scaler rows"
            )

        if (
            train_inputs.index[-1]
            >= validation_inputs.index[0]
            or scaler_rows.index[-1]
            >= validation_inputs.index[0]
        ):
            raise ValueError(
                "Validation must follow training"
            )

        return (
            train_inputs,
            labels,
            validation_inputs,
            validation_labels,
        )

    def _build_datasets(
        self,
        train_inputs: SequenceInput,
        train_labels: pd.Series,
        validation_inputs: SequenceInput,
        validation_labels: pd.Series,
    ) -> tuple[
        SequenceDataset,
        SequenceDataset,
        DataLoader,
        DataLoader,
    ]:
        cfg = self.config

        scaler_rows = (
            train_inputs.scaler_rows
        )

        if scaler_rows is None:
            raise ValueError(
                "Missing training scaler rows"
            )

        self.scaler = StandardScaler().fit(
            scaler_rows
        )

        self.scaler_fit_dates = {
            "start": (
                scaler_rows.index[
                    0
                ].isoformat()
            ),
            "end": (
                scaler_rows.index[
                    -1
                ].isoformat()
            ),
            "rows": len(
                scaler_rows
            ),
        }

        train_dataset = SequenceDataset(
            train_inputs,
            self.scaler,
            train_labels,
        )

        validation_dataset = SequenceDataset(
            validation_inputs,
            self.scaler,
            validation_labels,
        )

        generator = (
            torch.Generator()
            .manual_seed(
                cfg.random_state
            )
        )

        train_loader = DataLoader(
            train_dataset,
            batch_size=cfg.batch_size,
            shuffle=True,
            generator=generator,
            num_workers=0,
        )

        validation_loader = DataLoader(
            validation_dataset,
            batch_size=cfg.batch_size,
            shuffle=False,
            num_workers=0,
        )

        return (
            train_dataset,
            validation_dataset,
            train_loader,
            validation_loader,
        )

    def _build_training_objects(
        self,
        train_labels: pd.Series,
    ) -> tuple[
        nn.Module,
        nn.Module,
        torch.optim.Optimizer,
        Any,
    ]:
        cfg = self.config

        self.network = GRUNetwork(
            len(self.feature_names),
            cfg,
        ).to(
            self.device
        )

        balance = class_balance(
            train_labels
        )

        if cfg.balance_classes:
            self.pos_weight = (
                balance["negative"]
                / balance["positive"]
            )
        else:
            self.pos_weight = 1.0

        loss_fn = nn.BCEWithLogitsLoss(
            pos_weight=torch.tensor(
                self.pos_weight,
                device=self.device,
            )
        )

        # Validation deliberately uses ordinary,
        # unweighted binary log loss.
        validation_loss_fn = (
            nn.BCEWithLogitsLoss()
        )

        optimizer = torch.optim.AdamW(
            self.network.parameters(),
            lr=cfg.learning_rate,
            weight_decay=(
                cfg.weight_decay
            ),
        )

        scheduler = (
            torch.optim.lr_scheduler
            .ReduceLROnPlateau(
                optimizer,
                factor=(
                    cfg.scheduler_factor
                ),
                patience=(
                    cfg.scheduler_patience
                ),
            )
        )

        return (
            loss_fn,
            validation_loss_fn,
            optimizer,
            scheduler,
        )

    def _train_epoch(
        self,
        train_loader: DataLoader,
        loss_fn: nn.Module,
        optimizer: torch.optim.Optimizer,
    ) -> tuple[
        float,
        float,
        int,
    ]:
        cfg = self.config

        self.network.train()

        loss_sum = 0.0
        max_norm = 0.0
        clipped = 0

        for batch, labels in train_loader:
            batch = batch.to(
                self.device
            )

            labels = labels.to(
                self.device
            )

            optimizer.zero_grad(
                set_to_none=True
            )

            loss = loss_fn(
                self.network(batch),
                labels,
            )

            if not torch.isfinite(
                loss
            ):
                raise ValueError(
                    "Nonfinite GRU training loss"
                )

            loss.backward()

            norm = float(
                nn.utils.clip_grad_norm_(
                    self.network.parameters(),
                    cfg.gradient_clip,
                    error_if_nonfinite=True,
                )
            )

            max_norm = max(
                max_norm,
                norm,
            )

            clipped += int(
                norm > cfg.gradient_clip
            )

            optimizer.step()

            loss_sum += (
                float(
                    loss.detach()
                )
                * len(labels)
            )

        return (
            loss_sum,
            max_norm,
            clipped,
        )

    def _validation_loss(
        self,
        validation_loader: DataLoader,
        loss_fn: nn.Module,
        validation_size: int,
    ) -> float:
        self.network.eval()

        loss_sum = 0.0

        with torch.inference_mode():
            for (
                batch,
                labels,
            ) in validation_loader:
                batch = batch.to(
                    self.device
                )

                labels = labels.to(
                    self.device
                )

                loss = loss_fn(
                    self.network(batch),
                    labels,
                )

                loss_sum += (
                    float(loss)
                    * len(labels)
                )

        value = (
            loss_sum
            / validation_size
        )

        if not np.isfinite(
            value
        ):
            raise ValueError(
                "Nonfinite GRU validation loss"
            )

        return value

    def _capture_state(
        self,
    ) -> dict[str, torch.Tensor]:
        return {
            key: value
            .detach()
            .cpu()
            .clone()
            for key, value
            in self.network
            .state_dict()
            .items()
        }

    def _run_training_loop(
        self,
        train_dataset: SequenceDataset,
        validation_dataset: SequenceDataset,
        train_loader: DataLoader,
        validation_loader: DataLoader,
        loss_fn: nn.Module,
        validation_loss_fn: nn.Module,
        optimizer: torch.optim.Optimizer,
        scheduler: Any,
    ) -> tuple[
        float,
        int,
        float,
    ]:
        cfg = self.config

        self.history: list[
            dict[str, Any]
        ] = []

        best = float("inf")
        progress_best = float("inf")

        best_state: (
            dict[str, torch.Tensor]
            | None
        ) = None

        best_epoch = 0
        stale = 0

        synchronize(
            self.device
        )

        started = perf_counter()

        for epoch in range(
            1,
            cfg.max_epochs + 1,
        ):
            (
                train_loss_sum,
                max_norm,
                clipped,
            ) = self._train_epoch(
                train_loader,
                loss_fn,
                optimizer,
            )

            validation_loss = (
                self._validation_loss(
                    validation_loader,
                    validation_loss_fn,
                    len(
                        validation_dataset
                    ),
                )
            )

            train_loss = (
                train_loss_sum
                / len(train_dataset)
            )

            self.history.append(
                {
                    "epoch": epoch,
                    "train_loss": (
                        train_loss
                    ),
                    "validation_loss": (
                        validation_loss
                    ),
                    "learning_rate": (
                        optimizer
                        .param_groups[0][
                            "lr"
                        ]
                    ),
                    "max_gradient_norm_before_clip": (
                        max_norm
                    ),
                    "clipped_batches": (
                        clipped
                    ),
                }
            )

            scheduler.step(
                validation_loss
            )

            if validation_loss < best:
                best = validation_loss
                best_epoch = epoch
                best_state = (
                    self._capture_state()
                )

            if (
                validation_loss
                < progress_best
                - cfg.min_delta
            ):
                progress_best = (
                    validation_loss
                )
                stale = 0
            else:
                stale += 1

            logger.debug(
                "GRU epoch=%d train=%.5f val=%.5f",
                epoch,
                train_loss,
                validation_loss,
            )

            if stale >= cfg.patience:
                break

        if best_state is None:
            raise ValueError(
                "GRU training produced no valid checkpoint"
            )

        self.network.load_state_dict(
            best_state
        )

        self.network.eval()

        synchronize(
            self.device
        )

        return (
            best,
            best_epoch,
            perf_counter()
            - started,
        )

    def _set_training_diagnostics(
        self,
        train_dataset: SequenceDataset,
        validation_dataset: SequenceDataset,
        best: float,
        best_epoch: int,
        training_seconds: float,
    ) -> None:
        last = self.history[-1]

        self.training_diagnostics = {
            "best_epoch": (
                best_epoch
            ),
            "epochs_trained": len(
                self.history
            ),
            "best_validation_loss": (
                best
            ),
            "training_seconds": (
                training_seconds
            ),
            "device": str(
                self.device
            ),
            "torch_version": str(
                torch.__version__
            ),
            "parameter_count": (
                self.parameter_count
            ),
            "train_sequences": len(
                train_dataset
            ),
            "validation_sequences": len(
                validation_dataset
            ),
            "scaler_fit": (
                self.scaler_fit_dates
            ),
            "pos_weight": (
                self.pos_weight
            ),
            "overfit_warning": (
                last[
                    "validation_loss"
                ]
                - last[
                    "train_loss"
                ]
                > 0.15
            ),
            "validation_worse_than_best": (
                last[
                    "validation_loss"
                ]
                > best + 0.02
            ),
        }

    def fit(
        self,
        X: Any,
        y: pd.Series,
        *,
        validation: Any = None,
    ):
        (
            train_inputs,
            train_labels,
            validation_inputs,
            validation_labels,
        ) = self._prepare_training_inputs(
            X,
            y,
            validation,
        )

        cfg = self.config

        set_random_seeds(
            cfg.random_state,
            cfg.cpu_threads,
        )

        self.device = get_torch_device(
            cfg.device
        )

        (
            train_dataset,
            validation_dataset,
            train_loader,
            validation_loader,
        ) = self._build_datasets(
            train_inputs,
            train_labels,
            validation_inputs,
            validation_labels,
        )

        (
            loss_fn,
            validation_loss_fn,
            optimizer,
            scheduler,
        ) = self._build_training_objects(
            train_labels
        )

        (
            best,
            best_epoch,
            training_seconds,
        ) = self._run_training_loop(
            train_dataset,
            validation_dataset,
            train_loader,
            validation_loader,
            loss_fn,
            validation_loss_fn,
            optimizer,
            scheduler,
        )

        self._set_training_diagnostics(
            train_dataset,
            validation_dataset,
            best,
            best_epoch,
            training_seconds,
        )

        logger.info(
            (
                "GRU best_epoch=%d "
                "epochs=%d "
                "val_loss=%.5f "
                "seconds=%.2f"
            ),
            best_epoch,
            len(self.history),
            best,
            training_seconds,
        )

        return self

    def predict_proba(
        self,
        X: Any,
    ) -> np.ndarray:
        inputs = self._check_sequence(
            X
        )

        data = SequenceDataset(
            inputs,
            self.scaler,
        )

        loader = DataLoader(
            data,
            batch_size=(
                self.config.batch_size
            ),
            shuffle=False,
            num_workers=0,
        )

        self.network.eval()

        synchronize(
            self.device
        )

        started = perf_counter()

        probabilities: list[
            np.ndarray
        ] = []

        with torch.inference_mode():
            for batch in loader:
                logits = self.network(
                    batch.to(
                        self.device
                    )
                )

                probabilities.append(
                    torch.sigmoid(
                        logits
                    )
                    .cpu()
                    .numpy()
                )

        synchronize(
            self.device
        )

        self.last_inference_ms = (
            perf_counter()
            - started
        ) * 1000

        return np.concatenate(
            probabilities
        )

    def save_native(
        self,
        path: Path,
    ) -> dict[str, Any]:
        torch.save(
            {
                key: value
                .detach()
                .cpu()
                for key, value
                in self.network
                .state_dict()
                .items()
            },
            path / "gru.pt",
        )

        joblib.dump(
            self.scaler,
            path / "scaler.pkl",
        )

        (
            path
            / "configuration.json"
        ).write_text(
            json.dumps(
                asdict(
                    self.config
                ),
                indent=2,
            )
        )

        (
            path
            / "training_history.json"
        ).write_text(
            json.dumps(
                {
                    "epochs": (
                        self.history
                    ),
                    "diagnostics": (
                        self.training_diagnostics
                    ),
                },
                indent=2,
                allow_nan=False,
            )
        )

        return {
            "model_file": "gru.pt",
            "extra_files": [
                "scaler.pkl",
                "configuration.json",
                "training_history.json",
            ],
        }

    @classmethod
    def load_native(
        cls,
        path: Path,
        contract: dict[str, Any],
        device: str = "auto",
    ):
        saved = json.loads(
            (
                path
                / "configuration.json"
            ).read_text()
        )

        if (
            saved
            != contract[
                "model_parameters"
            ]
            or saved[
                "sequence_length"
            ]
            != contract[
                "sequence_length"
            ]
        ):
            raise ValueError(
                "GRU configuration mismatch"
            )

        model = cls(
            GRUConfig(**saved)
        )

        model.feature_names = tuple(
            contract[
                "feature_names"
            ]
        )

        model.device = get_torch_device(
            device
        )

        model.scaler = joblib.load(
            path / "scaler.pkl"
        )

        scaler_feature_names = getattr(
            model.scaler,
            "feature_names_in_",
            None,
        )

        if (
            scaler_feature_names
            is None
            or tuple(
                scaler_feature_names
            )
            != model.feature_names
        ):
            raise ValueError(
                "GRU scaler feature order mismatch"
            )

        model.network = GRUNetwork(
            len(
                model.feature_names
            ),
            model.config,
        )

        state_dict = torch.load(
            path / "gru.pt",
            map_location="cpu",
            weights_only=True,
        )

        if not isinstance(
            state_dict,
            dict,
        ):
            raise ValueError(
                "Invalid GRU state dictionary"
            )

        model.network.load_state_dict(
            state_dict,
            strict=True,
        )

        model.network.to(
            model.device
        ).eval()

        history = json.loads(
            (
                path
                / "training_history.json"
            ).read_text()
        )

        model.history = history[
            "epochs"
        ]

        model.training_diagnostics = (
            history[
                "diagnostics"
            ]
        )

        model.scaler_fit_dates = (
            model.training_diagnostics[
                "scaler_fit"
            ]
        )

        model.pos_weight = (
            model.training_diagnostics[
                "pos_weight"
            ]
        )

        return model