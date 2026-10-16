"""Evaluate cosine threshold on enrolled students; never changes the database."""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from school_face_id.repositories.recognition import load_recognition_embeddings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-far", type=float, default=0.001)
    args = parser.parse_args()
    student_ids, _, _, embeddings, _ = load_recognition_embeddings()
    if len(embeddings) < 2:
        raise SystemExit("At least two valid embeddings are required")
    matrix = np.asarray(embeddings, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms <= 1e-6):
        raise SystemExit("Zero-length embeddings remain in the database")
    matrix = matrix / norms
    genuine, impostor = [], []
    for left in range(len(matrix)):
        for right in range(left + 1, len(matrix)):
            score = float(matrix[left] @ matrix[right])
            (genuine if student_ids[left] == student_ids[right] else impostor).append(score)
    if not genuine:
        raise SystemExit(
            "No same-student pairs. Enroll 3-5 independent photos per student before tuning."
        )
    if not impostor:
        raise SystemExit("At least two different students are required for threshold tuning")

    candidates = np.linspace(0.20, 0.90, 701)
    rows = []
    for threshold in candidates:
        far = sum(score >= threshold for score in impostor) / len(impostor)
        frr = sum(score < threshold for score in genuine) / len(genuine)
        rows.append((float(threshold), far, frr))
    eligible = [row for row in rows if row[1] <= args.target_far]
    selected = min(eligible or rows, key=lambda row: (row[2], row[1], -row[0]))
    print(json.dumps({
        "students": len(set(student_ids)),
        "embeddings": len(embeddings),
        "genuine_pairs": len(genuine),
        "impostor_pairs": len(impostor),
        "target_far": args.target_far,
        "recommended_threshold": round(selected[0], 3),
        "measured_far": round(selected[1], 6),
        "measured_frr": round(selected[2], 6),
        "warning": "Validate this result on separate real camera captures before production.",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
