import os
from pathlib import Path

import cv2
from insightface.app import FaceAnalysis

from ..db.migrations import init_db
from ..recognition.camera_service import extract_face_embedding
from ..repositories.students import (
    add_face_embedding,
    delete_face_embeddings,
    get_students_by_name,
    save_student,
    update_imported_student,
)
from ..config.settings import FACE_DIR, FACE_MODEL_NAME, PROJECT_ROOT

# Файл атынын аягындагы бул суффикстер ракурс катары таанылат:
# Aigerim_front.jpg, Aigerim_left.jpg, Aigerim_right.jpg, Aigerim_up.jpg, ...
ANGLE_SUFFIXES = {"front", "left", "right", "up", "down", "left2", "right2"}


def _parse_name_and_angle(filename):
    stem = os.path.splitext(filename)[0].strip().replace(" ", "_")
    parts = stem.rsplit("_", 1)
    if len(parts) == 2 and parts[1].lower() in ANGLE_SUFFIXES:
        return parts[0], parts[1].lower()
    return stem, "front"


def import_photos_to_database(face_db_path=FACE_DIR, default_class="4_a"):
    """
    Папка түзүмү, мисалы:
        FACE_DIR/
            4_a/
                Aigerim_front.jpg
                Aigerim_left.jpg
                Aigerim_right.jpg
                Bakyt_front.jpg
                Bakyt_left.jpg
                ...
    Ар бир окуучуга канча ракурс/сүрөт койсоңуз, ошончосу face_embeddings
    таблицасына сакталат, ошондуктан окуучу камерага түз карабай, капталынан
    же башка бурчтан өтсө да таанылуу мүмкүнчүлүгү жогорулайт.

    Сүрөттөрдүн аты ракурс суффикссиз болсо да иштейт (мисалы жалгыз
    "Aigerim.jpg") — ал учурда бир гана "front" ракурсу катары сакталат,
    мурдагыдай эле.
    """
    init_db()

    app = FaceAnalysis(name=FACE_MODEL_NAME, providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))

    total_students = 0
    total_angles = 0
    print(f"Loading photos from {face_db_path} into school.db...")

    for root, _, files in os.walk(face_db_path):
        folder_name = os.path.basename(root)
        class_name = folder_name if folder_name != os.path.basename(face_db_path) else default_class

        # Бул папкадагы сүрөттөрдү окуучу боюнча топтойбуз, анткени бир
        # окуучунун бир нече ракурсу (файлы) болушу мүмкүн.
        photos_by_student = {}
        for filename in files:
            if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            name, angle_label = _parse_name_and_angle(filename)
            photos_by_student.setdefault(name, []).append(
                (os.path.join(root, filename), angle_label)
            )

        for name, photo_list in photos_by_student.items():
            # Кайра импорттогондо эски ракурстар кагылышып калбашы үчүн тазалайбыз
            matches = get_students_by_name(name)
            if len(matches) > 1:
                print(f"Skip {name}: multiple students have this name; use a unique student number")
                continue
            student_id = matches[0][0] if matches else None
            if student_id:
                delete_face_embeddings(student_id)

            saved_main = False
            angles_added = 0

            for photo_path, angle_label in photo_list:
                img = cv2.imread(photo_path)
                if img is None:
                    print(f"Skip {photo_path}: cannot read image")
                    continue

                embedding = extract_face_embedding(img, app)
                if embedding is None:
                    print(f"Skip {photo_path}: face not found")
                    continue

                if not saved_main:
                    # Биринчи ийгиликтүү сүрөт students таблицасына негизги
                    # (baseline) embedding катары сакталат — эски код менен
                    # шайкештик үчүн (мисалы админ панелдеги 1-сүрөт көрүнүшү).
                    relative_photo = Path(photo_path).resolve().relative_to(PROJECT_ROOT).as_posix()
                    if student_id:
                        update_imported_student(student_id, class_name, relative_photo, embedding)
                    else:
                        created = save_student(
                            name=name,
                            class_name=class_name,
                            photo_path=relative_photo,
                            embedding=embedding,
                        )
                        if not created:
                            print(f"Skip {name}: cannot create student")
                            break
                        student_id = created["id"]
                    saved_main = True
                    total_students += 1

                # Ар бир ийгиликтүү сүрөт/ракурс өзүнчө "галерея" катарында сакталат
                add_face_embedding(student_id, embedding, angle_label)
                angles_added += 1
                total_angles += 1
                print(f"Added {name} ({class_name}) [{angle_label}]")

            if angles_added == 0:
                print(f"WARNING: {name} үчүн бир дагы жарактуу сүрөт табылган жок")
            elif angles_added < 3:
                print(f"NOTE: {name} үчүн болгону {angles_added} ракурс — жакшыраак "
                      f"таануу үчүн 3-5 ракурс (front/left/right/up/down) сунушталат")

    print(f"Done. Added {total_students} students, {total_angles} face angles total.")
    return total_students


if __name__ == "__main__":
    import_photos_to_database()
