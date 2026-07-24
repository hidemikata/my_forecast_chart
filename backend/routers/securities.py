from fastapi import APIRouter, Query
from services import db

router = APIRouter()


@router.get("/securities/search")
def search_securities(
    q: str = Query("", max_length=50),
    limit: int = Query(10, ge=1, le=50),
):
    if not q.strip():
        return []
    return db.search_securities(q.strip(), limit)
