from fastapi import APIRouter

from .routers import (
    attendance, audit, auth, camera, classes, health, notifications, parents,
    realtime, school_attendance, students, teachers,
)

router = APIRouter(prefix="/api/v1")
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(attendance.router)
router.include_router(school_attendance.router)
router.include_router(classes.router)
router.include_router(audit.router)
router.include_router(notifications.router)
router.include_router(realtime.router)
router.include_router(parents.router)
router.include_router(students.router)
router.include_router(teachers.router)
router.include_router(camera.router)
