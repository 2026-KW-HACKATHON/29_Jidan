"""Run the real application under uvicorn for the demo E2E.

    python -m e2e.serve --port 8765

Nothing in the application changes. When `KAKAO_REST_API_KEY` is not set, Kakao Local is
replaced at the network edge only: `app.store_address` gets an httpx whose `Client` routes
https://dapi.kakao.com to a local MockTransport with fixed answers for the demo addresses. The
real address verification (exact match, postal code, administrative dong) still runs on those
answers. With a real key nothing is replaced and the real Kakao API is called.
"""
import argparse
import os
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import uvicorn

KAKAO_HOST = "https://dapi.kakao.com"
# canonical road address -> (postal code, x, y). Coordinates are looked up below.
ADDRESSES = {
    "서울특별시 노원구 광운로 20": ("01897", "127.0586", "37.6196"),
    "서울특별시 노원구 월계로 372": ("01905", "127.0590", "37.6260"),  # outside: 월계2동 in the fake
}
REGIONS = {
    ("127.0586", "37.6196"): "월계1동",
    ("127.0590", "37.6260"): "월계2동",
}


def kakao_answer(request: httpx.Request) -> httpx.Response:
    if not request.headers.get("authorization", "").startswith("KakaoAK "):
        return httpx.Response(401, json={"errorType": "AccessDeniedError"})
    params = {key: values[0] for key, values in parse_qs(request.url.query.decode()).items()}
    if request.url.path.endswith("/search/address.json"):
        # Match the way app.store_address compares (whitespace, "서울" = "서울특별시"): the real
        # postcode widget returns the short form ("서울 노원구 광운로 20"), and Kakao Local finds it.
        from app.store_address import canonical

        found = ADDRESSES.get(canonical(params.get("query", "")))
        documents = [] if found is None else [{
            "address_name": params["query"], "x": found[1], "y": found[2],
            "address": {"address_name": params["query"]},
            "road_address": {"address_name": params["query"], "zone_no": found[0]},
        }]
        return httpx.Response(200, json={"documents": documents})
    if request.url.path.endswith("/geo/coord2regioncode.json"):
        dong = REGIONS.get((params.get("x"), params.get("y")))
        documents = [] if dong is None else [
            {"region_type": "B", "region_1depth_name": "서울특별시", "region_2depth_name": "노원구",
             "region_3depth_name": "월계동"},
            {"region_type": "H", "region_1depth_name": "서울특별시", "region_2depth_name": "노원구",
             "region_3depth_name": dong},
        ]
        return httpx.Response(200, json={"documents": documents})
    return httpx.Response(404)


class KakaoFakeClient(httpx.Client):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("mounts", {KAKAO_HOST: httpx.MockTransport(kakao_answer)})
        super().__init__(*args, **kwargs)


def install_fake_kakao() -> None:
    from app import store_address

    os.environ["KAKAO_REST_API_KEY"] = "e2e-fake-kakao"
    store_address.httpx = SimpleNamespace(Client=KakaoFakeClient, HTTPError=httpx.HTTPError)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not os.getenv("KAKAO_REST_API_KEY", "").strip():
        install_fake_kakao()
        print("e2e.serve: KAKAO_REST_API_KEY not set; Kakao Local is answered by the local fake", flush=True)
    from e2e.ai_scenario import build_from_env

    provider = build_from_env()
    if provider is not None:  # before the app (and its task runner) starts
        from app.ai import set_ai_provider

        set_ai_provider(provider)
        print(f"e2e.serve: AI is the E2E {os.environ['E2E_AI']} provider", flush=True)
    from app.main import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
