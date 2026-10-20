from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ..dependencies import require_admin
from ...services import students as student_service

router = APIRouter(prefix="/students", tags=["students"])


async def decode_image(upload: UploadFile):
    from starlette.concurrency import run_in_threadpool
    from ...services.student_photos import InvalidPhotoError, normalize_photo

    await upload.seek(0)
    try:
        return await run_in_threadpool(normalize_photo, upload.file)
    except InvalidPhotoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def list_students(request: Request):
    require_admin(request)
    return student_service.list_students()


@router.post("/validate-face")
async def validate_face(request: Request, photo: Annotated[UploadFile, File()]):
    require_admin(request)
    image = await decode_image(photo)
    try:
        return student_service.face_quality(image)
    except student_service.FaceNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{student_id}/faces")
async def add_face_samples(
    student_id: str, request: Request, photos: Annotated[list[UploadFile], File()],
):
    require_admin(request)
    if not 1 <= len(photos) <= 5:
        raise HTTPException(status_code=400, detail="Upload between 1 and 5 photos")
    images = [await decode_image(photo) for photo in photos]
    try:
        return student_service.add_face_samples(student_id, images)
    except student_service.StudentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except student_service.FaceNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{student_id}/photo")
def student_photo(student_id: str, request: Request):
    require_admin(request)
    try:
        path = student_service.student_photo_path(student_id)
    except student_service.StudentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path)


@router.post("", status_code=201)
async def create_student(
    request: Request,
    name: Annotated[str, Form()],
    class_name: Annotated[str, Form()],
    photo: Annotated[UploadFile, File()],
    photo_left: Annotated[UploadFile, File()],
    photo_right: Annotated[UploadFile, File()],
    parent_name: Annotated[str, Form()] = "",
    parent_login: Annotated[str, Form()] = "",
    parent_phone: Annotated[str, Form()] = "",
    parent_password: Annotated[str, Form()] = "",
    parent_relationship: Annotated[str, Form()] = "guardian",
    photo_distance: UploadFile | None = File(None),
):
    require_admin(request)
    try:
        student_name = student_service.validate_new_student(name)
    except student_service.StudentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    image = await decode_image(photo)
    enrollment_images = [
        ("left", await decode_image(photo_left)),
        ("right", await decode_image(photo_right)),
    ]
    if photo_distance is not None and photo_distance.filename:
        enrollment_images.append(("distance", await decode_image(photo_distance)))
    try:
        from starlette.concurrency import run_in_threadpool

        return await run_in_threadpool(
            student_service.create_student,
            student_name,
            class_name,
            parent_name,
            parent_password,
            image,
            parent_login=parent_login,
            parent_phone=parent_phone,
            parent_relationship=parent_relationship,
            enrollment_images=enrollment_images,
        )
    except student_service.FaceNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except student_service.StudentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except student_service.PhotoSaveError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.put("/{student_id}")
async def edit_student(
    student_id: str,
    request: Request,
    name: Annotated[str, Form()],
    class_name: Annotated[str, Form()],
    parent_name: Annotated[str, Form()] = "",
    parent_login: Annotated[str, Form()] = "",
    parent_phone: Annotated[str, Form()] = "",
    parent_password: Annotated[str, Form()] = "",
    parent_relationship: Annotated[str, Form()] = "guardian",
    photo: UploadFile | None = File(None),
):
    require_admin(request)
    try:
        student_service.require_student(student_id)
    except student_service.StudentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    image = None
    if photo is not None and photo.filename:
        image = await decode_image(photo)

    try:
        return student_service.update_student(
            student_id,
            name,
            class_name,
            parent_name,
            parent_password,
            image,
            parent_login=parent_login,
            parent_phone=parent_phone,
            parent_relationship=parent_relationship,
        )
    except student_service.StudentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except student_service.FaceNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except student_service.StudentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except student_service.PhotoSaveError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/{student_id}")
def delete_student(student_id: str, request: Request):
    require_admin(request)
    try:
        return student_service.delete_student(student_id)
    except student_service.StudentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
