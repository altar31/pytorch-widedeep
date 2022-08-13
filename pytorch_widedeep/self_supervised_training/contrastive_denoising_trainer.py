import json
from pathlib import Path

import numpy as np
import torch
from tqdm import trange
from torch.utils.data import DataLoader, TensorDataset
from sklearn.model_selection import train_test_split

from pytorch_widedeep.wdtypes import (
    Dict,
    List,
    Tensor,
    Literal,
    Optional,
    Optimizer,
    LRScheduler,
    ModelWithAttention,
)
from pytorch_widedeep.callbacks import Callback
from pytorch_widedeep.preprocessing import TabPreprocessor
from pytorch_widedeep.training._trainer_utils import (
    save_epoch_logs,
    print_loss_and_metric,
)
from pytorch_widedeep.self_supervised_training._base_contrastive_denoising_trainer import (
    BaseContrastiveDenoisingTrainer,
)


class ContrastiveDenoisingTrainer(BaseContrastiveDenoisingTrainer):
    r"""This Class trains a Contrastive, Denoising Self Supervised 'routine' that
    is based on the one described in "SAINT: Improved Neural Networks for
    Tabular Data via Row Attention and Contrastive Pre-Training", their
    Figure 1.

    Parameters:
    -----------
    model: ModelWithAttention,
        ``ModelWithAttention`` object. Namely a model of class
        ``TabTransformer``, ``SAINT``, ``FTTransformer``, ``TabFastFormer``,
        ``TabPerceiver``, ``ContextAttentionMLP`` and ``SelfAttentionMLP``.
    preprocessor: ``TabPreprocessor``
        A fitted TabPreprocessor object. See
        :obj:`pytorch_widedeep.preprocessing.tab_preprocessor.TabPreprocessor`
    optimizer: ``Optimzer``, optional, default= None
        An instance of Pytorch's ``Optimizer`` object
        (e.g. :obj:`torch.optim.Adam()`). if no optimizer is passed it will
        default to ``AdamW``.
    lr_scheduler: ``LRScheduler``, optional, default=None
        An instance of Pytorch's ``LRScheduler`` object (e.g
        :obj:`torch.optim.lr_scheduler.StepLR(opt, step_size=5)`)
    callbacks: List, optional, default=None
        List with :obj:`Callback` objects. The three callbacks available in
        ``pytorch-widedeep`` are: ``LRHistory``, ``ModelCheckpoint`` and
        ``EarlyStopping``. The ``History`` and the ``LRShedulerCallback``
        callbacks are used by default. This can also be a custom callback as
        long as the object of type ``Callback``. See
        :obj:`pytorch_widedeep.callbacks.Callback` or the `Examples
        <https://github.com/jrzaurin/pytorch-widedeep/tree/master/examples>`__
        folder in the repo
    loss_type: str, default = "both"
        One of "contrastive", "denoising" or "both". See "SAINT: Improved
        Neural Networks for Tabular Data via Row Attention and Contrastive
        Pre-Training", their figure (1) and their equation (5).
    projection_head1_dims: list, Optional, default = None
        The projection heads are simply MLPs. 'projection_head1_dims' is a
        list of integers with the dimensions of the MLP hidden layers.
        See "SAINT: Improved Neural Networks for Tabular Data via Row
        Attention and Contrastive Pre-Training", their Figure 1
    projection_head2_dims: list, Optional, default = None
        Same as 'projection_head1_dims' for the second head
    projection_heads_activation: str, default = "relu"
        Activation function for the projection heads
    cat_mlp_type: str, default = "multiple"
        If 'denoising loss' is used, one can choose two types of 'stacked'
        MLPs to process the output from the transformer-based encoder that
        receives "corrupted" (cut-mixed and mixed-up) features. These
        are "single" or "multiple". The former approach will apply a single
        MLP to all the categorical features while the latter will use one MLP
        per categorical feature
    cont_mlp_type: str, default = "multiple"
        Same as 'cat_mlp_type' but for the continuous features
    denoise_mlps_activation: str, default = "relu"
        activation function for the so called 'denoising mlps'.
    verbose: int, default=1
        Setting it to 0 will print nothing during training.
    seed: int, default=1
        Random seed to be used internally for train_test_split
    """

    def __init__(
        self,
        model: ModelWithAttention,
        preprocessor: TabPreprocessor,
        optimizer: Optional[Optimizer] = None,
        lr_scheduler: Optional[LRScheduler] = None,
        callbacks: Optional[List[Callback]] = None,
        loss_type: Literal["contrastive", "denoising", "both"] = "both",
        projection_head1_dims: Optional[List[int]] = None,
        projection_head2_dims: Optional[List[int]] = None,
        projection_heads_activation: str = "relu",
        cat_mlp_type: Literal["single", "multiple"] = "multiple",
        cont_mlp_type: Literal["single", "multiple"] = "multiple",
        denoise_mlps_activation: str = "relu",
        verbose: int = 1,
        seed: int = 1,
        **kwargs,
    ):
        super().__init__(
            model=model,
            preprocessor=preprocessor,
            loss_type=loss_type,
            optimizer=optimizer,
            lr_scheduler=lr_scheduler,
            callbacks=callbacks,
            projection_head1_dims=projection_head1_dims,
            projection_head2_dims=projection_head2_dims,
            projection_heads_activation=projection_heads_activation,
            cat_mlp_type=cat_mlp_type,
            cont_mlp_type=cont_mlp_type,
            denoise_mlps_activation=denoise_mlps_activation,
            verbose=verbose,
            seed=seed,
            **kwargs,
        )

    def pretrain(
        self,
        X_tab: np.ndarray,
        X_val: Optional[np.ndarray] = None,
        val_split: Optional[float] = None,
        validation_freq: int = 1,
        n_epochs: int = 1,
        batch_size: int = 32,
    ):

        self.batch_size = batch_size

        train_set, eval_set = self._train_eval_split(X_tab, X_val, val_split)
        train_loader = DataLoader(
            dataset=train_set, batch_size=batch_size, num_workers=self.num_workers
        )
        train_steps = len(train_loader)
        if eval_set is not None:
            eval_loader = DataLoader(
                dataset=eval_set,
                batch_size=batch_size,
                num_workers=self.num_workers,
                shuffle=False,
            )
            eval_steps = len(eval_loader)

        self.callback_container.on_train_begin(
            {
                "batch_size": batch_size,
                "train_steps": train_steps,
                "n_epochs": n_epochs,
            }
        )
        for epoch in range(n_epochs):
            epoch_logs: Dict[str, float] = {}
            self.callback_container.on_epoch_begin(epoch, logs=epoch_logs)

            self.train_running_loss = 0.0
            with trange(train_steps, disable=self.verbose != 1) as t:
                for batch_idx, X in zip(t, train_loader):
                    t.set_description("epoch %i" % (epoch + 1))
                    train_loss = self._train_step(X[0], batch_idx)
                    self.callback_container.on_batch_end(batch=batch_idx)
                    print_loss_and_metric(t, train_loss)

            epoch_logs = save_epoch_logs(epoch_logs, train_loss, None, "train")

            on_epoch_end_metric = None
            if eval_set is not None and epoch % validation_freq == (
                validation_freq - 1
            ):
                self.callback_container.on_eval_begin()
                self.valid_running_loss = 0.0
                with trange(eval_steps, disable=self.verbose != 1) as v:
                    for batch_idx, X in zip(v, eval_loader):
                        v.set_description("valid")
                        val_loss = self._eval_step(X[0], batch_idx)
                        print_loss_and_metric(v, val_loss)
                epoch_logs = save_epoch_logs(epoch_logs, val_loss, None, "val")
                on_epoch_end_metric = val_loss
            else:
                if self.reducelronplateau:
                    raise NotImplementedError(
                        "ReduceLROnPlateau scheduler can be used only with validation data."
                    )

            self.callback_container.on_epoch_end(epoch, epoch_logs, on_epoch_end_metric)

            if self.early_stop:
                self.callback_container.on_train_end(epoch_logs)
                break

        self.callback_container.on_train_end(epoch_logs)
        self._restore_best_weights()
        self.cd_model.train()

    def save(
        self,
        path: str,
        save_state_dict: bool = False,
        model_filename: str = "cd_model.pt",
    ):
        save_dir = Path(path)
        history_dir = save_dir / "history"
        history_dir.mkdir(exist_ok=True, parents=True)

        # the trainer is run with the History Callback by default
        with open(history_dir / "train_eval_history.json", "w") as teh:
            json.dump(self.history, teh)  # type: ignore[attr-defined]

        has_lr_history = any(
            [clbk.__class__.__name__ == "LRHistory" for clbk in self.callbacks]
        )
        if self.lr_scheduler is not None and has_lr_history:
            with open(history_dir / "lr_history.json", "w") as lrh:
                json.dump(self.lr_history, lrh)  # type: ignore[attr-defined]

        model_path = save_dir / model_filename
        if save_state_dict:
            torch.save(self.cd_model.state_dict(), model_path)
        else:
            torch.save(self.cd_model, model_path)

    def _train_step(self, X_tab: Tensor, batch_idx: int):

        X = X_tab.to(self.device)

        self.optimizer.zero_grad()
        g_projs, cat_x_and_x_, cont_x_and_x_ = self.cd_model(X)
        loss = self._compute_loss(g_projs, cat_x_and_x_, cont_x_and_x_)
        loss.backward()
        self.optimizer.step()

        self.train_running_loss += loss.item()
        avg_loss = self.train_running_loss / (batch_idx + 1)

        return avg_loss

    def _eval_step(self, X_tab: Tensor, batch_idx: int):

        self.cd_model.eval()

        with torch.no_grad():
            X = X_tab.to(self.device)

            g_projs, cat_x_and_x_, cont_x_and_x_ = self.cd_model(X)
            loss = self._compute_loss(g_projs, cat_x_and_x_, cont_x_and_x_)

            self.valid_running_loss += loss.item()
            avg_loss = self.valid_running_loss / (batch_idx + 1)

        return avg_loss

    def _train_eval_split(
        self,
        X: np.ndarray,
        X_val: Optional[np.ndarray] = None,
        val_split: Optional[float] = None,
    ):

        if X_val is not None:
            train_set = TensorDataset(torch.from_numpy(X))
            eval_set = TensorDataset(torch.from_numpy(X_val))
        elif val_split is not None:
            X_tr, X_val = train_test_split(
                X, test_size=val_split, random_state=self.seed
            )
            train_set = TensorDataset(torch.from_numpy(X_tr))
            eval_set = TensorDataset(torch.from_numpy(X_val))
        else:
            train_set = TensorDataset(torch.from_numpy(X))
            eval_set = None

        return train_set, eval_set
