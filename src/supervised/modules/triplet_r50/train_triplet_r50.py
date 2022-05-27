import torch
import argparse
import configparser
import torchmetrics
import pytorch_lightning as pl
from torch import nn
from pytorch_lightning.loggers import WandbLogger
from panaf.datamodules import SupervisedPanAfDataModule
from src.supervised.models import SoftmaxEmbedderResNet50
from pytorch_metric_learning.miners import TripletMarginMiner
from miners import RandomNegativeTripletSelector
from losses import OnlineReciprocalTripletLoss
from sklearn.neighbors import KNeighborsClassifier


class ActionClassifier(pl.LightningModule):
    def __init__(self, lr, weight_decay):
        super().__init__()

        self.save_hyperparameters()

        self.embedder = SoftmaxEmbedderResNet50()
        self.classifier = KNeighborsClassifier(n_neighbors=9)

        self.triplet_miner = TripletMarginMiner(margin=0.1, type_of_triplets="easy")
        self.selector = RandomNegativeTripletSelector(margin=0.2)
        self.triplet_loss = OnlineReciprocalTripletLoss(self.selector)
        self.ce_loss = nn.CrossEntropyLoss()

        # Training metrics
        self.top1_train_accuracy = torchmetrics.Accuracy(top_k=1)
        self.train_per_class_accuracy = torchmetrics.Accuracy(
            num_classes=9, average="macro"
        )
        # Validation metrics
        self.top1_val_accuracy = torchmetrics.Accuracy(top_k=1)
        self.val_per_class_accuracy = torchmetrics.Accuracy(
            num_classes=9, average="macro"
        )

    def training_step(self, batch, batch_idx):

        x, y = batch
        embeddings, preds = self.embedder(x["spatial_sample"].permute(0, 2, 1, 3, 4))

        self.top1_train_accuracy(preds, y)
        self.train_per_class_accuracy(preds, y)

        a_idx, p_idx, n_idx = self.triplet_miner(embeddings, y)
        labels = torch.cat((y[a_idx], y[p_idx], y[n_idx]), dim=0)

        triplet_loss = self.triplet_loss(
            embeddings[a_idx],
            embeddings[p_idx],
            embeddings[n_idx],
            labels,
        )
        ce_loss = self.ce_loss(preds, y)
        loss = 0.01 * triplet_loss + ce_loss

        return {"loss": loss}

    def training_epoch_end(self, outputs):

        # Log epoch acc
        top1_acc = self.top1_train_accuracy.compute()
        self.log(
            "train_top1_acc_epoch",
            top1_acc,
            logger=True,
            on_epoch=True,
            on_step=False,
            prog_bar=True,
        )

        # Log per class epoch acc
        train_per_class_acc = self.train_per_class_accuracy.compute()
        self.log(
            "train_per_class_acc_epoch",
            train_per_class_acc,
            logger=True,
            on_epoch=True,
            on_step=False,
            prog_bar=True,
        )

        loss = torch.stack([x["loss"] for x in outputs]).mean()
        self.log(
            "train_loss_epoch",
            loss,
            logger=True,
            on_epoch=True,
            on_step=False,
            prog_bar=False,
        )

    def validation_step(self, batch, batch_idx):
        x, y = batch
        embeddings, preds = self.embedder(x["spatial_sample"].permute(0, 2, 1, 3, 4))

        self.top1_val_accuracy(preds, y)
        self.val_per_class_accuracy(preds, y)

        a_idx, p_idx, n_idx = self.triplet_miner(embeddings, y)
        labels = torch.cat((y[a_idx], y[p_idx], y[n_idx]), dim=0)

        triplet_loss = self.triplet_loss(
            embeddings[a_idx],
            embeddings[p_idx],
            embeddings[n_idx],
            labels,
        )
        ce_loss = self.ce_loss(preds, y)
        loss = 0.01 * triplet_loss + ce_loss

        return {"loss": loss}

    def validation_epoch_end(self, outputs):

        # Log epoch acc
        top1_acc = self.top1_val_accuracy.compute()
        self.log(
            "val_top1_acc_epoch",
            top1_acc,
            logger=True,
            on_epoch=True,
            on_step=False,
            prog_bar=True,
        )

        # Log per class epoch acc
        val_per_class_acc = self.val_per_class_accuracy.compute()
        self.log(
            "val_per_class_acc_epoch",
            val_per_class_acc,
            logger=True,
            on_epoch=True,
            on_step=False,
            prog_bar=True,
        )

        loss = torch.stack([x["loss"] for x in outputs]).mean()
        self.log(
            "val_loss_epoch",
            loss,
            logger=True,
            on_epoch=True,
            on_step=False,
            prog_bar=False,
        )

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(
            self.parameters(),
            lr=self.hparams.lr,
            weight_decay=self.hparams.weight_decay,
        )
        return optimizer


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, required=True)
    args = parser.parse_args()

    cfg = configparser.ConfigParser()
    cfg.read(args.config)

    data_module = SupervisedPanAfDataModule(cfg=cfg)
    model = ActionClassifier(
        lr=cfg.getfloat("hparams", "lr"),
        weight_decay=cfg.getfloat("hparams", "weight_decay"),
    )
    wand_logger = WandbLogger(offline=True)

    if cfg.getboolean("remote", "slurm"):
        trainer = pl.Trainer(
            gpus=cfg.getint("trainer", "gpus"),
            num_nodes=cfg.getint("trainer", "num_nodes"),
            strategy=cfg.get("trainer", "strategy"),
            max_epochs=cfg.getint("trainer", "max_epochs"),
            stochastic_weight_avg=cfg.getboolean("trainer", "swa"),
            logger=wand_logger,
        )
    else:
        trainer = pl.Trainer(
            gpus=cfg.getint("trainer", "gpus"),
            num_nodes=cfg.getint("trainer", "num_nodes"),
            strategy=cfg.get("trainer", "strategy"),
            max_epochs=cfg.getint("trainer", "max_epochs"),
            stochastic_weight_avg=cfg.getboolean("trainer", "swa"),
            fast_dev_run=5,
        )
    trainer.fit(model=model, datamodule=data_module)


if __name__ == "__main__":
    main()
