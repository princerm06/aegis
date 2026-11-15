"""Supervised training pipeline for AEGIS neural assignment models (MLP and Bipartite GNN).

Includes:
- Deterministic seeding across Python, NumPy, and PyTorch
- Vectorized padded tensor dataset builder
- Class-imbalance-weighted, tie-aware BCE + intra-scenario margin ranking loss
- Validation-set early stopping, loss curves, precision/recall/F1, and SHA-256 weight hashing
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import random
from time import perf_counter
from typing import Literal

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from neural_assignment import FEATURE_SCHEMA_VERSION, GENERATOR_VERSION
from neural_assignment.features import (
    CASUALTY_NODE_FEATURE_DIM,
    PAIR_FEATURE_DIM,
    RESPONDER_NODE_FEATURE_DIM,
    SceneFeatures,
    extract_features_from_bundle,
)
from neural_assignment.generator import GeneratorConfig, generate_split_bundles
from neural_assignment.models import BipartiteAssignmentGNN, PairwiseAssignmentMLP

ModelKind = Literal["mlp", "gnn"]


@dataclass
class PaddedDataset:
    pair_features: torch.Tensor  # (N, max_c, max_r, D_pair)
    cas_features: torch.Tensor  # (N, max_c, D_cas)
    res_features: torch.Tensor  # (N, max_r, D_res)
    valid_pair_mask: torch.Tensor  # (N, max_c, max_r) bool
    feasible_mask: torch.Tensor  # (N, max_c, max_r) bool
    teacher_targets: torch.Tensor  # (N, max_c, max_r) float32
    any_optimal_mask: torch.Tensor  # (N, max_c, max_r) bool
    tie_alt_mask: torch.Tensor  # (N, max_c, max_r) bool
    scene_features_list: list[SceneFeatures]


def set_deterministic_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def build_padded_dataset(
    bundles: list[dict],
    max_c: int = 6,
    max_r: int = 5,
) -> PaddedDataset:
    """Convert scenario bundles into a padded tensor dataset for batched training."""
    n = len(bundles)
    pair_feat = np.zeros((n, max_c, max_r, PAIR_FEATURE_DIM), dtype=np.float32)
    cas_feat = np.zeros((n, max_c, CASUALTY_NODE_FEATURE_DIM), dtype=np.float32)
    res_feat = np.zeros((n, max_r, RESPONDER_NODE_FEATURE_DIM), dtype=np.float32)
    valid_mask = np.zeros((n, max_c, max_r), dtype=bool)
    feas_mask = np.zeros((n, max_c, max_r), dtype=bool)
    targets = np.zeros((n, max_c, max_r), dtype=np.float32)
    any_opt = np.zeros((n, max_c, max_r), dtype=bool)
    tie_alt = np.zeros((n, max_c, max_r), dtype=bool)
    sf_list: list[SceneFeatures] = []

    for idx, bundle in enumerate(bundles):
        sf = extract_features_from_bundle(bundle)
        sf_list.append(sf)
        nc, nr = sf.feasible_mask.shape
        pair_feat[idx, :nc, :nr] = sf.pair_features
        cas_feat[idx, :nc] = sf.casualty_node_features
        res_feat[idx, :nr] = sf.responder_node_features
        valid_mask[idx, :nc, :nr] = True
        feas_mask[idx, :nc, :nr] = sf.feasible_mask

        c_index = {cid: i for i, cid in enumerate(sf.casualty_ids)}
        r_index = {rid: j for j, rid in enumerate(sf.responder_ids)}
        for cid, rid in bundle["teacher_pairs"]:
            targets[idx, c_index[cid], r_index[rid]] = 1.0
        for cid, rid in bundle["any_optimal_pairs"]:
            any_opt[idx, c_index[cid], r_index[rid]] = True
        for cid, rid in bundle["tie_alternative_pairs"]:
            tie_alt[idx, c_index[cid], r_index[rid]] = True

    return PaddedDataset(
        pair_features=torch.from_numpy(pair_feat),
        cas_features=torch.from_numpy(cas_feat),
        res_features=torch.from_numpy(res_feat),
        valid_pair_mask=torch.from_numpy(valid_mask),
        feasible_mask=torch.from_numpy(feas_mask),
        teacher_targets=torch.from_numpy(targets),
        any_optimal_mask=torch.from_numpy(any_opt),
        tie_alt_mask=torch.from_numpy(tie_alt),
        scene_features_list=sf_list,
    )


def compute_tie_aware_loss(
    logits: torch.Tensor,
    dataset_batch: dict[str, torch.Tensor],
    pos_weight: float,
    infeasible_weight: float = 0.25,
    tie_soft_target: float = 0.75,
    tie_sample_weight: float = 0.35,
    ranking_weight: float = 0.30,
    margin: float = 0.80,
) -> torch.Tensor:
    """Compute weighted tie-aware BCE + intra-scenario competition ranking loss.

    - Feasible pairs omitted only due to arbitrary entity-ID tie-breaking (`tie_alt_mask`)
      are given a soft target (`tie_soft_target`) and reduced weight (`tie_sample_weight`)
      rather than penalized as hard negatives.
    - Feasible pairs receive full supervision weight (1.0) while infeasible pairs receive
      `infeasible_weight` (0.25) so the network focuses capacity on feasible competition
      while also learning to score infeasible pairs below 0.
    - Intra-scenario pairwise margin loss encourages optimal feasible edges to outscore
      competing suboptimal feasible edges on the same casualty or responder.
    """
    valid = dataset_batch["valid_pair_mask"]
    feas = dataset_batch["feasible_mask"]
    targets = dataset_batch["teacher_targets"]
    any_opt = dataset_batch["any_optimal_mask"]
    tie_alt = dataset_batch["tie_alt_mask"]

    soft_targets = torch.where(
        tie_alt, torch.full_like(targets, tie_soft_target), targets
    )
    pw_tensor = torch.tensor(pos_weight, dtype=logits.dtype, device=logits.device)
    raw_bce = F.binary_cross_entropy_with_logits(
        logits, soft_targets, pos_weight=pw_tensor, reduction="none"
    )

    sample_weights = torch.where(
        feas, torch.ones_like(targets), torch.full_like(targets, infeasible_weight)
    )
    sample_weights = torch.where(
        tie_alt, torch.full_like(targets, tie_sample_weight), sample_weights
    )
    sample_weights = sample_weights * valid.to(logits.dtype)
    bce_loss = (raw_bce * sample_weights).sum() / sample_weights.sum().clamp_min(1.0)

    if ranking_weight <= 0.0:
        return bce_loss

    # Vectorized intra-scenario competition margin ranking over feasible edges:
    # Compare optimal feasible edges (any_opt & feas) vs suboptimal feasible edges (~any_opt & feas)
    pos_mask = feas & any_opt & valid  # (B, C, R)
    neg_mask = feas & (~any_opt) & valid  # (B, C, R)

    # 1. Same casualty i: pos edge (b, i, j_pos) vs neg edge (b, i, j_neg)
    diff_cas = logits.unsqueeze(3) - logits.unsqueeze(2)  # (B, C, R_pos, R_neg)
    pair_cas_mask = pos_mask.unsqueeze(3) & neg_mask.unsqueeze(2)
    hinge_cas = F.relu(margin - diff_cas) * pair_cas_mask.to(logits.dtype)
    loss_cas = hinge_cas.sum() / pair_cas_mask.sum().clamp_min(1).to(logits.dtype)

    # 2. Same responder j: pos edge (b, i_pos, j) vs neg edge (b, i_neg, j)
    diff_res = logits.unsqueeze(2) - logits.unsqueeze(1)  # (B, C_pos, C_neg, R)
    pair_res_mask = pos_mask.unsqueeze(2) & neg_mask.unsqueeze(1)
    hinge_res = F.relu(margin - diff_res) * pair_res_mask.to(logits.dtype)
    loss_res = hinge_res.sum() / pair_res_mask.sum().clamp_min(1).to(logits.dtype)

    return bce_loss + ranking_weight * 0.5 * (loss_cas + loss_res)


@torch.no_grad()
def evaluate_validation_metrics(
    model: nn.Module,
    dataset: PaddedDataset,
    pos_weight: float,
) -> dict:
    """Compute validation loss and pairwise classification metrics on feasible edges."""
    model.eval()
    logits = model(
        dataset.pair_features,
        dataset.cas_features,
        dataset.res_features,
        dataset.feasible_mask,
        dataset.valid_pair_mask,
    )
    batch_dict = {
        "valid_pair_mask": dataset.valid_pair_mask,
        "feasible_mask": dataset.feasible_mask,
        "teacher_targets": dataset.teacher_targets,
        "any_optimal_mask": dataset.any_optimal_mask,
        "tie_alt_mask": dataset.tie_alt_mask,
    }
    val_loss = float(compute_tie_aware_loss(logits, batch_dict, pos_weight=pos_weight).item())

    # Evaluate pairwise precision/recall/F1 on non-tie feasible pairs
    eval_mask = dataset.feasible_mask & dataset.valid_pair_mask & (~dataset.tie_alt_mask)
    preds = (logits >= 0.0) & eval_mask
    truths = (dataset.teacher_targets >= 0.5) & eval_mask

    tp = int((preds & truths).sum().item())
    fp = int((preds & (~truths)).sum().item())
    fn = int(((~preds) & truths).sum().item())

    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2.0 * precision * recall / max(precision + recall, 1e-8)

    # Also measure raw unfiltered precision/recall across all valid pairs
    all_valid = dataset.valid_pair_mask & (~dataset.tie_alt_mask)
    all_preds = (logits >= 0.0) & all_valid
    all_truths = (dataset.teacher_targets >= 0.5) & all_valid
    all_tp = int((all_preds & all_truths).sum().item())
    all_fp = int((all_preds & (~all_truths)).sum().item())
    all_fn = int(((~all_preds) & all_truths).sum().item())
    all_prec = all_tp / max(all_tp + all_fp, 1)
    all_rec = all_tp / max(all_tp + all_fn, 1)
    all_f1 = 2.0 * all_prec * all_rec / max(all_prec + all_rec, 1e-8)

    return {
        "val_loss": round(val_loss, 5),
        "feasible_precision": round(precision, 4),
        "feasible_recall": round(recall, 4),
        "feasible_f1": round(f1, 4),
        "all_pairs_precision": round(all_prec, 4),
        "all_pairs_recall": round(all_rec, 4),
        "all_pairs_f1": round(all_f1, 4),
    }


def instantiate_model(model_kind: ModelKind, hidden_dim: int | None = None) -> nn.Module:
    if model_kind == "mlp":
        h = hidden_dim or 128
        return PairwiseAssignmentMLP(hidden_dim=h, mid_dim=max(h // 2, 32))
    if model_kind == "gnn":
        h = hidden_dim or 96
        return BipartiteAssignmentGNN(hidden_dim=h, num_layers=3)
    raise ValueError(f"Unsupported model kind: {model_kind}")


def train_model(
    model_kind: ModelKind,
    train_dataset: PaddedDataset,
    val_dataset: PaddedDataset,
    seed: int = 42,
    epochs: int = 35,
    batch_size: int = 128,
    lr: float = 2.5e-3,
    weight_decay: float = 1e-4,
    patience: int = 8,
    hidden_dim: int | None = None,
    save_path: Path | None = None,
) -> tuple[nn.Module, dict]:
    """Train an MLP or Bipartite GNN with early stopping and record complete provenance."""
    set_deterministic_seed(seed)
    started = perf_counter()

    model = instantiate_model(model_kind, hidden_dim=hidden_dim)
    param_count = sum(p.numel() for p in model.parameters() if p.requires_grad)

    # Calculate positive class weight over feasible training edges
    feas_valid = train_dataset.feasible_mask & train_dataset.valid_pair_mask
    n_pos = float((train_dataset.any_optimal_mask & feas_valid).sum().item())
    n_neg = float(((~train_dataset.any_optimal_mask) & feas_valid).sum().item())
    pos_weight = round(max(1.0, min(n_neg / max(n_pos, 1.0), 4.0)), 4)

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=epochs, eta_min=lr * 0.05
    )

    n_train = train_dataset.pair_features.shape[0]
    best_val_loss = float("inf")
    best_val_metrics: dict = {}
    best_epoch = 0
    best_state_dict = {k: v.detach().clone() for k, v in model.state_dict().items()}
    epochs_without_improvement = 0
    history: list[dict] = []

    rng = np.random.default_rng(seed)

    for epoch in range(1, epochs + 1):
        model.train()
        perm = rng.permutation(n_train)
        epoch_loss_sum = 0.0
        n_batches = 0

        for start_idx in range(0, n_train, batch_size):
            batch_idx = torch.from_numpy(perm[start_idx : start_idx + batch_size])
            p_feat = train_dataset.pair_features[batch_idx]
            c_feat = train_dataset.cas_features[batch_idx]
            r_feat = train_dataset.res_features[batch_idx]
            v_mask = train_dataset.valid_pair_mask[batch_idx]
            f_mask = train_dataset.feasible_mask[batch_idx]
            t_targ = train_dataset.teacher_targets[batch_idx]
            a_opt = train_dataset.any_optimal_mask[batch_idx]
            t_alt = train_dataset.tie_alt_mask[batch_idx]

            optimizer.zero_grad()
            logits = model(p_feat, c_feat, r_feat, f_mask, v_mask)
            loss = compute_tie_aware_loss(
                logits,
                {
                    "valid_pair_mask": v_mask,
                    "feasible_mask": f_mask,
                    "teacher_targets": t_targ,
                    "any_optimal_mask": a_opt,
                    "tie_alt_mask": t_alt,
                },
                pos_weight=pos_weight,
            )
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=2.0)
            optimizer.step()

            epoch_loss_sum += float(loss.item())
            n_batches += 1

        scheduler.step()
        train_loss = epoch_loss_sum / max(n_batches, 1)
        val_metrics = evaluate_validation_metrics(model, val_dataset, pos_weight=pos_weight)
        val_loss = val_metrics["val_loss"]

        history.append(
            {
                "epoch": epoch,
                "train_loss": round(train_loss, 5),
                **val_metrics,
            }
        )

        if val_loss < best_val_loss - 1e-4:
            best_val_loss = val_loss
            best_val_metrics = dict(val_metrics)
            best_epoch = epoch
            best_state_dict = {k: v.detach().clone() for k, v in model.state_dict().items()}
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= patience:
                break

    model.load_state_dict(best_state_dict)
    model.eval()
    runtime_s = perf_counter() - started

    weight_sha256: str | None = None
    if save_path is not None:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint = {
            "model_kind": model_kind,
            "seed": seed,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "generator_version": GENERATOR_VERSION,
            "hidden_dim": getattr(model, "hidden_dim", None),
            "state_dict": model.state_dict(),
        }
        torch.save(checkpoint, save_path)
        weight_sha256 = hashlib.sha256(save_path.read_bytes()).hexdigest()
    else:
        # Hash serialized state dict tensors deterministically even if not saved to disk
        hasher = hashlib.sha256()
        for k in sorted(best_state_dict.keys()):
            hasher.update(k.encode())
            hasher.update(best_state_dict[k].cpu().numpy().tobytes())
        weight_sha256 = hasher.hexdigest()

    metadata = {
        "model_kind": model_kind,
        "seed": seed,
        "parameter_count": param_count,
        "train_scenarios": n_train,
        "val_scenarios": int(val_dataset.pair_features.shape[0]),
        "epochs_configured": epochs,
        "epochs_ran": len(history),
        "best_epoch": best_epoch,
        "early_stopped": len(history) < epochs,
        "patience": patience,
        "batch_size": batch_size,
        "learning_rate": lr,
        "weight_decay": weight_decay,
        "pos_weight": pos_weight,
        "training_runtime_seconds": round(runtime_s, 3),
        "best_val_metrics": best_val_metrics,
        "weight_sha256": weight_sha256,
        "weight_file": save_path.name if save_path is not None else None,
        "loss_curve_summary": {
            "epoch_1_train_loss": history[0]["train_loss"],
            "epoch_1_val_loss": history[0]["val_loss"],
            "best_val_loss": best_val_loss,
            "final_train_loss": history[-1]["train_loss"],
            "final_val_loss": history[-1]["val_loss"],
        },
        "history": history,
    }
    return model, metadata


def load_trained_model(checkpoint_path: Path) -> tuple[nn.Module, dict]:
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    model_kind: ModelKind = checkpoint["model_kind"]
    hidden_dim = checkpoint.get("hidden_dim")
    model = instantiate_model(model_kind, hidden_dim=hidden_dim)
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    return model, checkpoint


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train supervised MLP and Bipartite GNN assignment models."
    )
    parser.add_argument("--models", nargs="+", choices=["mlp", "gnn"], default=["mlp", "gnn"])
    parser.add_argument("--seeds", nargs="+", type=int, default=[42, 43, 44])
    parser.add_argument("--train-count", type=int, default=5000)
    parser.add_argument("--val-count", type=int, default=600)
    parser.add_argument("--epochs", type=int, default=35)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--weights-dir", type=str, default="weights")
    parser.add_argument("--reports-dir", type=str, default="reports")
    args = parser.parse_args()

    cfg = GeneratorConfig(train_count=args.train_count, val_count=args.val_count)
    train_bundles, train_stats = generate_split_bundles("train", config=cfg)
    val_bundles, val_stats = generate_split_bundles("val", config=cfg)

    train_ds = build_padded_dataset(train_bundles)
    val_ds = build_padded_dataset(val_bundles)

    weights_dir = Path(args.weights_dir)
    reports_dir = Path(args.reports_dir)
    weights_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    runs: list[dict] = []
    for model_kind in args.models:
        for seed in args.seeds:
            ckpt_path = weights_dir / f"{model_kind}_seed{seed}.pt"
            _, meta = train_model(
                model_kind=model_kind,
                train_dataset=train_ds,
                val_dataset=val_ds,
                seed=seed,
                epochs=args.epochs,
                batch_size=args.batch_size,
                save_path=ckpt_path,
            )
            runs.append(meta)

    summary = {
        "generator_config": cfg.to_dict(),
        "train_split_stats": train_stats,
        "val_split_stats": val_stats,
        "runs": runs,
    }
    (reports_dir / "training_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps(
            {
                "train_count": train_stats["count"],
                "val_count": val_stats["count"],
                "models_trained": [
                    {
                        "model": r["model_kind"],
                        "seed": r["seed"],
                        "best_epoch": r["best_epoch"],
                        "runtime_s": r["training_runtime_seconds"],
                        "feasible_f1": r["best_val_metrics"]["feasible_f1"],
                        "sha256": r["weight_sha256"][:12],
                    }
                    for r in runs
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
