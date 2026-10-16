"""Strict validation for official fine-tuned cross-domain FAS checkpoints."""

from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path


class CheckpointError(RuntimeError):
    pass


def file_sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _state_summary(state):
    prefixes = Counter(key.split(".", 1)[0] for key in state)
    return {
        "key_count": len(state),
        "top_level_prefix_counts": dict(sorted(prefixes.items())),
        "first_keys": list(state)[:10],
        "last_keys": list(state)[-10:],
    }


def inspect_state_dict(model, state):
    expected = model.state_dict()
    missing = sorted(set(expected) - set(state))
    unexpected = sorted(set(state) - set(expected))
    shape_mismatches = [
        {"key": key, "expected": list(expected[key].shape), "actual": list(state[key].shape)}
        for key in sorted(set(expected) & set(state))
        if tuple(expected[key].shape) != tuple(state[key].shape)
    ]
    return missing, unexpected, shape_mismatches


def load_fas_checkpoint(model, path: Path, device="cpu"):
    import torch

    resolved = path.resolve()
    metadata = {
        "absolute_path": str(resolved),
        "sha256": file_sha256(resolved),
        "size_bytes": resolved.stat().st_size,
    }
    # Official FAS checkpoints store NumPy scalar validation metadata alongside
    # tensors.  PyTorch 2.3's restricted loader cannot deserialize that scalar,
    # so use the same explicit pickle loading behavior as upstream.  Checkpoints
    # must therefore come from the trusted official repository.
    checkpoint = torch.load(resolved, map_location="cpu", weights_only=False)
    if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("state_dict"), dict):
        raise CheckpointError(
            "Expected an official fine-tuned cross-domain FAS checkpoint with a state_dict; "
            "a generic pre-training checkpoint is not accepted"
        )
    state = checkpoint["state_dict"]
    metadata["checkpoint_keys"] = sorted(checkpoint.keys())
    metadata["state_dict_summary"] = _state_summary(state)
    required = {
        "backbone.vit.patch_embed.proj.weight",
        "backbone.vit.blocks.11.attn.qkv.weight",
        "backbone.vit.fc_norm.weight",
        "embedder.bottleneck_layer_fc.weight",
        "classifier.classifier_layer.weight",
    }
    absent_signature = sorted(required - set(state))
    if absent_signature:
        raise CheckpointError(f"Checkpoint is not ViT-S/16 FAS; missing signature keys: {absent_signature}")
    missing, unexpected, mismatches = inspect_state_dict(model, state)
    metadata.update({
        "non_strict_loading_result": {
            "missing_keys": missing,
            "unexpected_keys": unexpected,
            "shape_mismatches": mismatches,
        },
        "missing_keys": missing,
        "unexpected_keys": unexpected,
        "strict_loading_result": "not attempted",
    })
    if missing or unexpected or mismatches:
        raise CheckpointError(
            "Checkpoint does not strictly match official ViT-S/16 FAS architecture: "
            f"missing={missing}, unexpected={unexpected}, shape_mismatches={mismatches}"
        )
    model.load_state_dict(state, strict=True)
    metadata["strict_loading_result"] = "success"
    metadata["checkpoint_metadata"] = {
        key: checkpoint[key] for key in (
            "epoch", "valid_arg", "best_model_EER", "best_model_ACER",
            "best_model_ACC", "threshold",
        ) if key in checkpoint
    }
    model.to(device)
    model.eval()
    return metadata
