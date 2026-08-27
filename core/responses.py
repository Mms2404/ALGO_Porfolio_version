"""Standard response envelopes (SmartAPI-shaped). Shared by the broker layer
and the DRF API views."""


def success_response(data=None, message="SUCCESS"):
    return {"status": True, "message": message, "errorcode": "", "data": data or {}}


def error_response(message="Something went wrong", errorcode="AB_INTERNAL_ERROR", data=None):
    return {"status": False, "message": message, "errorcode": errorcode, "data": data}