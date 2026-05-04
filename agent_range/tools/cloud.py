"""Mock cloud-API tool — dispatches to the in-process cloud provider.

Endpoints supported:

    GET    /resources                       list all resources
    GET    /resources/<id>                  fetch one resource
    POST   /resources/<id>/snapshot         snapshot a resource
    DELETE /resources/<id>                  destroy a resource
"""
from __future__ import annotations

from .. import cloud_provider
from .base import register


@register("call_cloud_api")
def call_cloud_api(args: dict) -> dict:
    method = args["method"].upper()
    endpoint = args["endpoint"]
    token = args["token"]

    parts = [p for p in endpoint.split("/") if p]

    if method == "GET" and parts == ["resources"]:
        return {"resources": cloud_provider.list_resources(token)}

    if method == "GET" and len(parts) == 2 and parts[0] == "resources":
        return cloud_provider.get_resource(token, parts[1])

    if method == "DELETE" and len(parts) == 2 and parts[0] == "resources":
        return cloud_provider.delete_resource(token, parts[1])

    if (
        method == "POST"
        and len(parts) == 3
        and parts[0] == "resources"
        and parts[2] == "snapshot"
    ):
        return cloud_provider.snapshot_resource(token, parts[1])

    raise ValueError(f"unknown endpoint: {method} {endpoint}")
