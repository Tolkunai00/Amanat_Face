import numpy as np


def normalize_embedding(embedding):
    vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
    norm = np.linalg.norm(vector)
    return vector / max(float(norm), 1e-6)


def match_identity_details(embedding, student_ids, names, parent_ids, classes, matrix, threshold, margin):
    """Match against identities; margin compares distinct people, not photos."""
    if len(matrix) == 0:
        return {"matched": False, "reason": "UNKNOWN_FACE", "score": None, "margin": None}
    gallery = np.asarray(matrix, dtype=np.float32)
    gallery = gallery / np.maximum(np.linalg.norm(gallery, axis=1, keepdims=True), 1e-6)
    scores = gallery @ normalize_embedding(embedding)
    best_by_identity = {}
    for index, (student_id, score) in enumerate(zip(student_ids, scores)):
        score = float(score)
        if student_id not in best_by_identity or score > best_by_identity[student_id][0]:
            best_by_identity[student_id] = (score, index)
    ranked = sorted(best_by_identity.values(), key=lambda item: item[0], reverse=True)
    best_score, best_index = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else -1.0
    score_margin = best_score - second_score if len(ranked) > 1 else None
    result = {
        "student_id": student_ids[best_index],
        "name": names[best_index],
        "parent_id": parent_ids[best_index],
        "class_name": classes[best_index],
        "score": best_score,
        "top2_score": second_score if len(ranked) > 1 else None,
        "margin": score_margin,
    }
    if best_score < threshold:
        return {**result, "matched": False, "reason": "LOW_SIMILARITY"}
    if score_margin is not None and score_margin < margin:
        return {**result, "matched": False, "reason": "LOW_MARGIN"}
    return {**result, "matched": True, "reason": ""}


def match_identity(embedding, student_ids, names, parent_ids, classes, matrix, threshold, margin):
    result = match_identity_details(
        embedding, student_ids, names, parent_ids, classes, matrix, threshold, margin
    )
    return result if result["matched"] else None
