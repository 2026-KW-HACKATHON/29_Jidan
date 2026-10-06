"""Kakao Local checks a canonical building address, postal code and administrative dong."""
import os

import httpx

from app.errors import ApiError, ErrorCode

# The only administrative dong (행정동) a store may be in. Every store row passed this check, and
# the stored address is Kakao's road address, which does not name the dong. So the dong of any
# stored store is this value; it cannot be derived from `stores.address` itself.
SERVICE_NEIGHBORHOOD = "월계1동"


def address_error(field: str):
    return ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[
        {"field": "store." + field, "code": "INVALID_FORMAT", "message": "주소와 우편번호를 확인해 주세요."},
    ])


def canonical(value: str) -> str:
    value = " ".join(value.split())
    if value.startswith("서울 "):
        value = "서울특별시 " + value[3:]
    return value


def verify_store_address(store) -> str:
    key = os.getenv("KAKAO_REST_API_KEY", "").strip()
    if not key:
        raise ApiError(500, ErrorCode.INTERNAL_ERROR)
    try:
        with httpx.Client(timeout=5, follow_redirects=False, headers={"Authorization": "KakaoAK " + key}) as client:
            def get(path, **params):
                result = client.get("https://dapi.kakao.com/v2/local/" + path + ".json", params=params)
                if result.status_code != 200:
                    raise ApiError(500, ErrorCode.INTERNAL_ERROR)
                documents = result.json().get("documents")
                if not isinstance(documents, list):
                    raise ApiError(500, ErrorCode.INTERNAL_ERROR)
                return documents
            candidates = get("search/address", query=store.address, analyze_type="exact", size=30)
            exact = [row for row in candidates if isinstance(row, dict)
                     and isinstance(row.get("road_address"), dict)
                     and any(isinstance(a, dict) and canonical(a.get("address_name", "")) == canonical(store.address)
                             for a in (row.get("address"), row.get("road_address")))]
            if len(exact) != 1:
                raise address_error("address")
            row = exact[0]
            road = row["road_address"]
            if road.get("zone_no") != store.postalCode:
                raise address_error("postalCode")
            regions = get("geo/coord2regioncode", x=row["x"], y=row["y"])
            administrative = [r for r in regions if r.get("region_type") == "H"]
            if len(administrative) != 1:
                raise address_error("address")
            region = administrative[0]
            if (region.get("region_1depth_name"), region.get("region_2depth_name"), region.get("region_3depth_name")) != (
                "서울특별시", "노원구", SERVICE_NEIGHBORHOOD,
            ):
                raise ApiError(422, ErrorCode.STORE_OUTSIDE_SERVICE_AREA)
            return road["address_name"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        # No remote payload, URL with submitted address, or API key enters logs/responses.
        raise ApiError(500, ErrorCode.INTERNAL_ERROR) from None
