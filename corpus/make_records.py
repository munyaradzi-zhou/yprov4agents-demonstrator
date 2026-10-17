"""Produce a corpus of real yProv4ML records from real training runs.

The reference run replays two records shipped with yProv4ML's examples. Two records cannot
carry a claim about decisions on records in general, so this trains small models for real
and lets yProv4ML record each run, varying what a training script varies: model width,
learning rate, batch size, epoch count, and whether a checkpoint is written mid-run. Every
record is produced by the library under study, not written by hand.

yProv4ML pins rdflib 6 and pulls torch, neither of which belongs in this project's
environment, so it runs in its own:

    uv venv corpus/.venv -p 3.11
    uv pip install -p corpus/.venv yprov4ml==3.2.7
    corpus/.venv/Scripts/python corpus/make_records.py --runs 24 --out C:/yprov4ml-corpus

yProv4ML writes absolute paths into the record, so generate somewhere whose path names no
user, then copy the ``prov_*.json`` files (not the weights and metric CSVs) into
``corpus/yprov4ml/``.

Two things are adjusted, both identity rather than content. yProv4ML 3.2.7 imports the
Unix-only ``pwd`` module to name the user, so on Windows a stand-in is installed that names
a fixed corpus user; and the host name is fixed for the same reason, so a record does not
carry the machine it was generated on into a public repository.
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import socket
import sys
import time
import types
from pathlib import Path

CORPUS_USER = "yprov4agents-corpus"
CORPUS_HOST = "corpus-host"


def _portable_identity() -> None:
    if "pwd" not in sys.modules:
        try:
            import pwd  # noqa: F401
        except ImportError:
            stand_in = types.ModuleType("pwd")
            stand_in.getpwuid = lambda _uid: (CORPUS_USER,)  # type: ignore[attr-defined]
            sys.modules["pwd"] = stand_in
    if not hasattr(os, "getuid"):
        os.getuid = lambda: 0  # type: ignore[attr-defined]
    socket.gethostname = lambda: CORPUS_HOST


def _train(run: int, rng: random.Random, out: Path) -> Path:
    import torch
    import yprov4ml

    width = rng.choice([8, 16, 32, 64])
    lr = rng.choice([1e-3, 3e-3, 1e-2, 3e-2])
    batch = rng.choice([16, 32, 64])
    epochs = rng.randint(2, 6)
    checkpoint_at = rng.randint(1, epochs) if rng.random() < 0.5 else None

    torch.manual_seed(run)
    x = torch.randn(512, 4)
    y = (x @ torch.tensor([1.5, -2.0, 0.5, 1.0]) + 0.1 * torch.randn(512)).unsqueeze(1)
    train, val = (x[:384], y[:384]), (x[384:], y[384:])
    model = torch.nn.Sequential(
        torch.nn.Linear(4, width), torch.nn.ReLU(), torch.nn.Linear(width, 1)
    )
    optimiser = torch.optim.SGD(model.parameters(), lr=lr)
    loss_fn = torch.nn.MSELoss()

    run_dir = out / f"run_{run:02d}"
    yprov4ml.start_run(
        experiment_name=f"corpus_{run:02d}",
        provenance_save_dir=str(run_dir),
        save_after_n_logs=100,
        use_run_id=run,
    )
    yprov4ml.log_param("width", width)
    yprov4ml.log_param("learning_rate", lr)
    yprov4ml.log_param("batch_size", batch)
    yprov4ml.log_param("epochs", epochs)

    for epoch in range(epochs):
        # One context per epoch, as a script that wants per-epoch lineage declares it.
        epoch_context = f"Epoch_{epoch}"
        yprov4ml.log_context(epoch_context, is_subcontext_of="Training")
        model.train()
        for start in range(0, len(train[0]), batch):
            optimiser.zero_grad()
            inputs, targets = train[0][start : start + batch], train[1][start : start + batch]
            loss = loss_fn(model(inputs), targets)
            loss.backward()
            optimiser.step()
        yprov4ml.log_metric("MSELoss", loss.item(), context=epoch_context, step=epoch)
        yprov4ml.log_system_metrics(context=epoch_context, step=epoch)
        model.eval()
        with torch.no_grad():
            val_loss = loss_fn(model(val[0]), val[1])
        yprov4ml.log_metric("MSELoss", val_loss.item(), context="Validation", step=epoch)
        if checkpoint_at == epoch + 1:
            yprov4ml.save_model_version(f"model_epoch_{epoch}", model, context=epoch_context)

    with torch.no_grad():
        test_loss = loss_fn(model(val[0][:64]), val[1][:64])
    yprov4ml.log_metric("MSELoss", test_loss.item(), context="Testing", step=0)
    yprov4ml.log_model("model_final", model, context="Training")
    yprov4ml.end_run(create_graph=False, create_svg=False)
    return run_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=24)
    parser.add_argument("--seed", type=int, default=20261017)
    parser.add_argument("--out", type=Path, default=Path("corpus/yprov4ml"))
    args = parser.parse_args(argv)

    _portable_identity()
    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    # yProv4ML writes model copies and a requirements.txt into the working directory.
    os.chdir(out)
    rng = random.Random(args.seed)
    for run in range(args.runs):
        started = time.perf_counter()
        run_dir = _train(run, rng, out)
        print(f"[corpus] run {run:02d} -> {run_dir} ({time.perf_counter() - started:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
