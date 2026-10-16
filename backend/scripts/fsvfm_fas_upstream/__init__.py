"""Minimal diagnostic-only FS-VFM cross-domain FAS implementation.

Derived from the official ``wolo-wolo/FSFM-CVPR25``
``fsvfm/finetune/cross_domain_FAS`` code.  It is intentionally isolated from
the Amanat production recognition package.
"""

from .checkpoint import CheckpointError, file_sha256, load_fas_checkpoint
from .model import FSVFMFAS, build_vit_small_fas
from .preprocess import OFFICIAL_PROFILE, PreprocessProfile, preprocess_face

__all__ = [
    "CheckpointError", "FSVFMFAS", "OFFICIAL_PROFILE", "PreprocessProfile",
    "build_vit_small_fas", "file_sha256", "load_fas_checkpoint", "preprocess_face",
]
