from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, WebSocket, status

from .bridge import Bridge
from .fleet import FleetService
from .hub import Hub
from .jobs import JobService
from .schemas import JobCreate, JobDetail, JobView, RobotView, SiteView, Snapshot


@dataclass
class Services:
    fleet: FleetService
    jobs: JobService
    hub: Hub
    bridge: Bridge | None

    async def snapshot(self) -> Snapshot:
        return Snapshot(
            site=await self.fleet.site(),
            robots=await self.fleet.robots(),
            jobs=await self.jobs.recent(),
        )


def services(request: Request) -> Services:
    return request.app.state.services


Svc = Annotated[Services, Depends(services)]

router = APIRouter(prefix="/api")


@router.get("/health")
async def health(svc: Svc) -> dict[str, object]:
    return {
        "status": "ok",
        "broker_connected": bool(svc.bridge and svc.bridge.connected),
        "live_clients": svc.hub.client_count,
    }


@router.get("/site")
async def site(svc: Svc) -> SiteView:
    return await svc.fleet.site()


@router.get("/robots")
async def robots(svc: Svc) -> list[RobotView]:
    return await svc.fleet.robots()


@router.post("/robots/{robot_id}/enroll", status_code=status.HTTP_202_ACCEPTED)
async def enroll(robot_id: str, svc: Svc) -> RobotView:
    return await svc.fleet.request_enrollment(robot_id, enroll=True)


@router.post("/robots/{robot_id}/unenroll", status_code=status.HTTP_202_ACCEPTED)
async def unenroll(robot_id: str, svc: Svc) -> RobotView:
    return await svc.fleet.request_enrollment(robot_id, enroll=False)


@router.get("/jobs")
async def list_jobs(svc: Svc, limit: Annotated[int, Query(ge=1, le=1000)] = 200) -> list[JobView]:
    return await svc.jobs.recent(limit)


@router.delete("/jobs/finished")
async def clear_finished(svc: Svc) -> dict[str, int]:
    return {"cleared": await svc.jobs.clear_finished()}


@router.get("/jobs/{job_id}")
async def job_detail(job_id: str, svc: Svc) -> JobDetail:
    return await svc.jobs.detail(job_id)


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
async def create_job(
    body: JobCreate,
    svc: Svc,
    response: Response,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> JobView:
    job, created = await svc.jobs.create(body, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    return job


@router.post("/jobs/{job_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_job(job_id: str, svc: Svc) -> JobView:
    return await svc.jobs.request_cancel(job_id)


@router.websocket("/ws")
async def live(websocket: WebSocket) -> None:
    svc: Services = websocket.app.state.services
    await svc.hub.serve(websocket, await svc.snapshot())
