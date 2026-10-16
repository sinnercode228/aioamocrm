from __future__ import annotations

import httpx
import pytest

from aioamocrm import (
    AmoCRMError,
    AuthError,
    ForbiddenError,
    NotFoundError,
    PaymentRequiredError,
    RateLimitError,
    ServerError,
    ValidationError,
    ValidationIssue,
)
from aioamocrm._errors import error_from_response

URL = "https://example.amocrm.ru/api/v4/leads"

# Shape of a 400 answer to POST /api/v4/leads, as given in the amoCRM v4 documentation.
VALIDATION_BODY = {
    "validation-errors": [
        {
            "request_id": "0",
            "errors": [
                {
                    "code": "NotSupportedChoice",
                    "path": "custom_fields_values.0.field_id",
                    "detail": "The value you selected is not a valid choice.",
                }
            ],
        },
        {
            "request_id": "1",
            "errors": [
                {
                    "code": "InvalidType",
                    "path": "price",
                    "detail": "This value should be of type int.",
                },
                {
                    "code": "NotBlank",
                    "path": "name",
                    "detail": "This value should not be blank.",
                },
            ],
        },
    ],
    "title": "Bad Request",
    "type": "https://httpstatus.es/400",
    "status": 400,
    "detail": "Request validation failed",
}


def response(status: int, json: object = None, **kwargs: object) -> httpx.Response:
    request = httpx.Request("POST", URL)
    if json is None:
        return httpx.Response(status, request=request, **kwargs)  # type: ignore[arg-type]
    return httpx.Response(status, json=json, request=request, **kwargs)  # type: ignore[arg-type]


def test_validation_errors_are_parsed_per_request_id() -> None:
    error = error_from_response(response(400, VALIDATION_BODY))

    assert isinstance(error, ValidationError)
    assert error.status_code == 400
    assert error.title == "Bad Request"
    assert error.detail == "Request validation failed"
    assert error.errors == [
        ValidationIssue(
            request_id="0",
            code="NotSupportedChoice",
            path="custom_fields_values.0.field_id",
            detail="The value you selected is not a valid choice.",
        ),
        ValidationIssue(
            request_id="1",
            code="InvalidType",
            path="price",
            detail="This value should be of type int.",
        ),
        ValidationIssue(
            request_id="1",
            code="NotBlank",
            path="name",
            detail="This value should not be blank.",
        ),
    ]
    text = str(error)
    assert "HTTP 400" in text
    assert "POST /api/v4/leads" in text
    assert "custom_fields_values.0.field_id: The value you selected is not a valid choice." in text


def test_a_400_without_validation_details_is_still_a_validation_error() -> None:
    body = {"title": "Bad Request", "status": 400, "detail": "Invalid JSON"}
    error = error_from_response(response(400, body))
    assert isinstance(error, ValidationError)
    assert error.errors == []
    assert error.detail == "Invalid JSON"


def test_malformed_validation_entries_are_skipped() -> None:
    body = {"validation-errors": [None, {"request_id": 3, "errors": [{"path": "name"}, 5]}]}
    error = error_from_response(response(400, body))
    assert isinstance(error, ValidationError)
    assert error.errors == [ValidationIssue(request_id="3", code=None, path="name", detail=None)]


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, AuthError),
        (402, PaymentRequiredError),
        (403, ForbiddenError),
        (404, NotFoundError),
        (429, RateLimitError),
        (500, ServerError),
        (502, ServerError),
        (504, ServerError),
        (418, AmoCRMError),
    ],
)
def test_status_codes_map_to_typed_errors(status: int, kind: type[AmoCRMError]) -> None:
    body = {"title": "T", "type": f"https://httpstatus.es/{status}", "status": status, "detail": "D"}
    error = error_from_response(response(status, body))
    assert type(error) is kind
    assert error.status_code == status
    assert error.body == body
    assert error.method == "POST"
    assert error.url == URL


def test_payment_required_names_the_account_problem() -> None:
    body = {
        "title": "Payment Required",
        "type": "https://httpstatus.es/402",
        "status": 402,
        "detail": "Account is not paid",
    }
    error = error_from_response(response(402, body))
    assert isinstance(error, PaymentRequiredError)
    assert "Account is not paid" in str(error)


def test_retry_after_is_kept_on_rate_limit_errors() -> None:
    error = error_from_response(response(429, headers={"Retry-After": "3"}))
    assert isinstance(error, RateLimitError)
    assert error.retry_after == 3.0


def test_non_json_bodies_are_kept_as_text() -> None:
    error = error_from_response(response(502, content=b"<html>Bad Gateway</html>"))
    assert isinstance(error, ServerError)
    assert error.body == "<html>Bad Gateway</html>"
    assert error.title is None


def test_long_text_bodies_are_cut_in_the_message() -> None:
    error = error_from_response(response(500, content=b"x" * 5000))
    assert len(str(error)) < 600


def test_query_strings_do_not_reach_the_message() -> None:
    request = httpx.Request("GET", URL + "?query=%2B79990001122")
    error = error_from_response(httpx.Response(404, request=request))
    assert "79990001122" not in str(error)
    assert "GET /api/v4/leads" in str(error)


def test_errors_carry_batch_context_defaults() -> None:
    error = AmoCRMError("boom")
    assert error.batch_offset is None
    assert error.completed == []
    assert str(error) == "boom"
