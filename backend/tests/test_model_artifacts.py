import hashlib

import pytest

from school_face_id.config.model_artifacts import ModelArtifactError, validate_model_artifact


@pytest.mark.parametrize("setting_name", ["ANTI_SPOOFING_MODEL_PATH", "FSVFM_CHECKPOINT"])
def test_model_artifact_good_sha_is_accepted(tmp_path, setting_name):
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(b"verified-model")
    expected = hashlib.sha256(artifact.read_bytes()).hexdigest()
    result = validate_model_artifact(str(artifact), expected, setting_name)
    assert result.sha256 == expected
    assert result.size_bytes > 0


@pytest.mark.parametrize("setting_name", ["ANTI_SPOOFING_MODEL_PATH", "FSVFM_CHECKPOINT"])
def test_model_artifact_bad_sha_is_rejected(tmp_path, setting_name):
    artifact = tmp_path / "model.bin"
    artifact.write_bytes(b"wrong-model")
    with pytest.raises(ModelArtifactError, match=f"{setting_name}_SHA256 mismatch"):
        validate_model_artifact(str(artifact), "0" * 64, setting_name)
