from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ..dependencies import require_admin
from ...config.settings import MAX_UPLOAD_BYTES
from ...services import students as student_service

router = APIRouter(prefix="/students", tags=["students"])


async def decode_image(upload: UploadFile):
    import cv2
    import numpy as np

    content_type = (upload.content_type or "").lower()
    if content_type and not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image uploads are allowed")
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image is too large")
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Cannot read image")
    return image


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
    parent_name: Annotated[str, Form()] = "",
    parent_login: Annotated[str, Form()] = "",
    parent_phone: Annotated[str, Form()] = "",
    parent_password: Annotated[str, Form()] = "",
    parent_relationship: Annotated[str, Form()] = "guardian",
):
    require_admin(request)
    try:
        student_name = student_service.validate_new_student(name)
    except student_service.StudentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    image = await decode_image(photo)
    try:
        return student_service.create_student(
            student_name,
            class_name,
            parent_name,
            parent_password,
            image,
            parent_login=parent_login,
            parent_phone=parent_phone,
            parent_relationship=parent_relationship,
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
